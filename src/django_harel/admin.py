"""The executions in the Django admin: where each one is, and the control plane's commands.

Read-only: an execution changes through its machine, or through the actions here (suspend,
resume, cancel, terminate, purge), which go through the distributed runner — a cooperative
cancel's cleanup runs on a worker, like the machine's other actions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from django.contrib import admin, messages
from django.db import connections
from django.db.models.expressions import RawSQL
from django.utils.html import format_html
from harel.engine.control import PurgeRefused
from harel.engine.execution import Status

from django_harel import conf
from django_harel.models import HarelExecution
from django_harel.store import DjangoStore, json_field


def _when(timestamp) -> str:
    if timestamp is None:
        return "—"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _pretty(value) -> str:
    return format_html("<pre>{}</pre>", json.dumps(value, indent=2, sort_keys=True, default=str))


class _JsonFieldFilter(admin.SimpleListFilter):
    """Filter on a field inside the Execution JSON."""

    field = ""

    def queryset(self, request, queryset):
        if self.value() is None:
            return queryset
        vendor = connections[queryset.db].vendor
        return queryset.annotate(**{f"_{self.field}": RawSQL(json_field(vendor, self.field), [])}).filter(
            **{f"_{self.field}": self.value()}
        )


class StatusFilter(_JsonFieldFilter):
    title = "status"
    parameter_name = "status"
    field = "status"

    def lookups(self, request, model_admin):
        return [(s.value, s.value.lower()) for s in Status]


class RootsFilter(admin.SimpleListFilter):
    title = "kind"
    parameter_name = "roots"

    def lookups(self, request, model_admin):
        return [("1", "roots only")]

    def queryset(self, request, queryset):
        if self.value() != "1":
            return queryset
        vendor = connections[queryset.db].vendor
        return queryset.annotate(_parent_id=RawSQL(json_field(vendor, "parent_id"), [])).filter(
            _parent_id__isnull=True
        )


# the change page: everything read-only
_DETAIL = (
    "id",
    "definition_id",
    "status",
    "outcome",
    "active_state",
    "error",
    "created",
    "updated",
    "finished",
    "parent",
    "children",
    "context",
    "trace",
    "version",
)


@admin.register(HarelExecution)
class HarelExecutionAdmin(admin.ModelAdmin):
    list_display = ["id", "definition_id", "status", "outcome", "active_state", "updated", "version"]
    list_filter = [StatusFilter, "definition_id", RootsFilter]
    search_fields = ["=id", "^id", "definition_id"]
    ordering = ["id"]
    list_per_page = 50
    fields = _DETAIL
    readonly_fields = _DETAIL
    actions = ["suspend", "resume", "cancel", "terminate", "purge"]

    # --- read-only ---------------------------------------------------------------------------
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # `purge` deletes a whole finished tree, the way harel allows it

    def has_operate_permission(self, request):
        """Who may run the control-plane actions: whoever may change an execution."""
        return request.user.has_perm("django_harel.change_harelexecution")

    # --- columns -----------------------------------------------------------------------------
    @admin.display(description="status")
    def status(self, obj):
        return obj.snapshot.get("status")

    @admin.display(description="outcome")
    def outcome(self, obj):
        return obj.snapshot.get("outcome") or "—"

    @admin.display(description="state")
    def active_state(self, obj):
        return obj.snapshot.get("active_path") or "—"

    @admin.display(description="error")
    def error(self, obj):
        return obj.snapshot.get("error") or "—"

    @admin.display(description="created")
    def created(self, obj):
        return _when(obj.snapshot.get("created_at"))

    @admin.display(description="updated")
    def updated(self, obj):
        return _when(obj.snapshot.get("updated_at"))

    @admin.display(description="finished")
    def finished(self, obj):
        return _when(obj.snapshot.get("finished_at"))

    @admin.display(description="parent")
    def parent(self, obj):
        return obj.snapshot.get("parent_id") or "—"

    @admin.display(description="children")
    def children(self, obj):
        return _pretty(obj.snapshot.get("children") or {})

    @admin.display(description="context")
    def context(self, obj):
        return _pretty(obj.snapshot.get("context") or {})

    @admin.display(description="trace")
    def trace(self, obj):
        steps = DjangoStore(obj._state.db or "default").read_trace(obj.id)
        return _pretty(steps) if steps else "— (recorded with DJANGO_HAREL['TRACE'] = True)"

    # --- the control plane -------------------------------------------------------------------
    def _each(self, request, queryset, verb: str, command) -> None:
        done, failed = 0, []
        for obj in queryset:
            try:
                command(obj.id)
                done += 1
            except (KeyError, PurgeRefused, ValueError) as exc:
                failed.append(f"{obj.id}: {exc}")
        if done:
            self.message_user(request, f"{verb} {done} execution(s).", messages.SUCCESS)
        for failure in failed:
            self.message_user(request, failure, messages.ERROR)

    @admin.action(description="Suspend the selected executions", permissions=["operate"])
    def suspend(self, request, queryset):
        self._each(request, queryset, "Suspended", conf.distributed_runner().suspend)

    @admin.action(description="Resume the selected executions", permissions=["operate"])
    def resume(self, request, queryset):
        self._each(request, queryset, "Resumed", conf.distributed_runner().resume)

    @admin.action(
        description="Cancel the selected executions (the machine's cleanup)", permissions=["operate"]
    )
    def cancel(self, request, queryset):
        self._each(request, queryset, "Cancelled", conf.distributed_runner().cancel)

    @admin.action(description="Terminate the selected executions (no cleanup)", permissions=["operate"])
    def terminate(self, request, queryset):
        self._each(request, queryset, "Terminated", conf.distributed_runner().terminate)

    @admin.action(description="Purge the selected finished executions", permissions=["operate"])
    def purge(self, request, queryset):
        self._each(request, queryset, "Purged", conf.distributed_runner().purge)
