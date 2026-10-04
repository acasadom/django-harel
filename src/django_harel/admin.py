"""The executions in the Django admin: where each one is, and the control plane's commands.

Read-only: an execution changes through its machine, or through the actions here (suspend,
resume, cancel, terminate, purge), which go through the distributed runner — a cooperative
cancel's cleanup runs on a worker, like the machine's other actions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.db import connections
from django.db.models.expressions import RawSQL
from django.template.response import TemplateResponse
from django.utils.html import format_html
from harel import engine
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
    actions = ["suspend", "resume", "cancel", "terminate", "redrive", "purge"]

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

    @admin.action(description="Redrive the selected failed executions…", permissions=["operate"])
    def redrive(self, request, queryset):
        """Ask for the state to resume at, then bring the failed executions back there."""
        runner = conf.distributed_runner()
        executions = [e for e in (runner.store.load(obj.id) for obj in queryset) if e is not None]
        if not executions or any(e.status is not Status.FAILED for e in executions):
            self.message_user(request, "Redrive takes failed executions only.", messages.ERROR)
            return None
        definition_ids = {e.definition_id for e in executions}
        if len(definition_ids) > 1:
            self.message_user(request, "Redrive the executions of one machine at a time.", messages.ERROR)
            return None
        (definition_id,) = definition_ids
        defn = conf.definitions().get(definition_id)
        if defn is None:
            self.message_user(
                request, f"The machine {definition_id!r} isn't in DJANGO_HAREL['MACHINES'].", messages.ERROR
            )
            return None
        targets = _redrive_targets(defn, executions)
        if not targets:
            self.message_user(request, "No state those executions could resume at.", messages.ERROR)
            return None

        target = request.POST.get("target")
        if request.POST.get("apply") and target in targets:
            self._each(
                request, queryset, "Redrove", lambda execution_id: runner.redrive(execution_id, target)
            )
            return None

        failed_at = {e.active_path for e in executions}
        return TemplateResponse(
            request,
            "admin/django_harel/redrive.html",
            {
                **self.admin_site.each_context(request),
                "title": "Redrive failed executions",
                "opts": self.model._meta,
                "executions": executions,
                "definition_id": definition_id,
                "targets": targets,
                # where they failed, when that's one state they can resume at
                "suggested": next(iter(failed_at)) if len(failed_at) == 1 else None,
                "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            },
        )

    @admin.action(description="Purge the selected finished executions", permissions=["operate"])
    def purge(self, request, queryset):
        self._each(request, queryset, "Purged", conf.distributed_runner().purge)


def _redrive_targets(defn, executions) -> list[str]:
    """The states every one of `executions` can be redriven to: leaves of its own branch,
    outside any orthogonal state — harel's rule — and not a terminal, which would end it."""
    return sorted(
        node.full_path
        for node in defn.index.values()
        if node.outcome is None
        and all(engine.is_valid_reposition_target(defn, e, node.full_path) for e in executions)
    )
