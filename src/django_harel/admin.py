"""The executions in the Django admin: where each one is, and the control plane's commands.

Read-only: an execution changes through its machine, or through the actions here (suspend,
resume, cancel, terminate, purge), which go through the distributed runner — a cooperative
cancel's cleanup runs on a worker, like the machine's other actions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.admin.utils import quote
from django.core.exceptions import PermissionDenied
from django.db import connections
from django.db.models import Exists, OuterRef
from django.db.models.expressions import RawSQL
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from harel import engine
from harel.engine.control import PurgeRefused
from harel.engine.execution import Execution, Status

from django_harel import conf, inspection
from django_harel.models import HarelExecution, HarelMachine
from django_harel.store import DjangoStore, json_field


def _when(timestamp) -> str:
    if timestamp is None:
        return "—"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _pretty(value) -> str:
    return format_html("<pre>{}</pre>", json.dumps(value, indent=2, sort_keys=True, default=str))


def _compact(value) -> str:
    if not value:
        return "—"
    return format_html('<code class="harel-data">{}</code>', json.dumps(value, sort_keys=True, default=str))


# a fan-out can have thousands of children, a queue thousands of events: show the first ones
_MAX_ROWS = 50


def _table(headers, rows, more: int = 0) -> str:
    head = format_html_join("", "<th>{}</th>", ((h,) for h in headers))
    body = format_html_join(
        "", "<tr>{}</tr>", ((format_html_join("", "<td>{}</td>", ((c,) for c in row)),) for row in rows)
    )
    tail = format_html('<p class="harel-muted">… and {} more</p>', more) if more > 0 else ""
    return format_html(
        '<table class="harel-table"><thead><tr>{}</tr></thead><tbody>{}</tbody></table>{}', head, body, tail
    )


def _execution_link(execution_id: str) -> str:
    url = reverse("admin:django_harel_harelexecution_change", args=[quote(execution_id)])
    return format_html('<a href="{}"><code>{}</code></a>', url, execution_id)


def diagram_source(defn, active: list[str]) -> str:
    """The Mermaid source of `defn`'s diagram, with the states in `active` highlighted. A state
    the machine no longer has (its `.stm` changed since) is drawn without the highlight."""
    from harel.viz.mermaid import render

    try:
        return render(defn, active=active)
    except ValueError:
        return render(defn)


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


class TopLevelFilter(admin.SimpleListFilter):
    """ "Top-level only": an execution worth looking at on its own — no parent, or a parent
    that runs a *different* machine (an invoked submachine's own entry point). Excludes
    orthogonal regions, which only make sense nested inside their parent's tree (see the
    Machines page, and `inspection.top_level_units`)."""

    title = "kind"
    parameter_name = "top_level"

    def lookups(self, request, model_admin):
        return [("1", "top-level only")]

    def queryset(self, request, queryset):
        if self.value() != "1":
            return queryset
        vendor = connections[queryset.db].vendor
        qs = queryset.annotate(_parent_id=RawSQL(json_field(vendor, "parent_id"), []))
        same_machine_parent = HarelExecution.objects.filter(
            id=OuterRef("_parent_id"), definition_id=OuterRef("definition_id")
        )
        return qs.filter(~Exists(same_machine_parent))


# the change page: everything read-only
_FIELDSETS: Any = (
    (None, {"fields": ("id", "definition_id", "status", "outcome", "active_state", "error", "version")}),
    ("When", {"fields": ("created", "updated", "finished")}),
    ("Machine", {"fields": ("diagram",)}),
    ("Parent and children", {"fields": ("tree",)}),
    ("Waiting", {"fields": ("pending",)}),
    ("Timeline", {"fields": ("timeline",)}),
    ("Context", {"fields": ("context",)}),
)
_DETAIL = tuple(name for _, options in _FIELDSETS for name in options["fields"])


class _DrawsDiagrams:
    """Mixin for an admin that renders a Mermaid diagram (`HarelExecutionAdmin`'s own, and
    `HarelMachineAdmin`'s on a page outside the built-in change form, which doesn't get
    `Media` for free — see `HarelMachineAdmin.machine_detail_view`)."""

    class Media:
        js = (conf.get_setting("MERMAID_JS"), "django_harel/admin.js")
        css = {"all": ("django_harel/admin.css",)}


@admin.register(HarelExecution)
class HarelExecutionAdmin(_DrawsDiagrams, admin.ModelAdmin):
    list_display = ["id", "definition_id", "status", "outcome", "active_state", "updated", "version"]
    list_filter = [StatusFilter, "definition_id", TopLevelFilter]
    search_fields = ["=id", "^id", "definition_id"]
    ordering = ["id"]
    list_per_page = 50
    fieldsets = _FIELDSETS
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

    @admin.display(description="context")
    def context(self, obj):
        return _pretty(obj.snapshot.get("context") or {})

    # --- the machine, the tree, what it waits for, what it did ---------------------------------
    def _store(self, obj) -> DjangoStore:
        return DjangoStore(obj._state.db or "default")

    @admin.display(description="diagram")
    def diagram(self, obj):
        exe = Execution.model_validate_json(obj.data)
        defn = inspection.machine_for(exe)
        if defn is None:
            return format_html(
                '<span class="harel-muted">{} isn\'t in DJANGO_HAREL["MACHINES"]</span>', exe.definition_id
            )
        active = inspection.active_paths(exe, self._store(obj))
        return format_html(
            '<pre class="harel-diagram">{}</pre><p class="harel-active">In: {}</p>',
            diagram_source(defn, active),
            format_html_join(", ", "<code>{}</code>", ((p,) for p in active)) if active else "—",
        )

    @admin.display(description="tree")
    def tree(self, obj):
        """This execution's whole tree (`inspection.execution_tree`): itself, its orthogonal
        regions and invoked children, and theirs — indented by depth. A region never gets its
        own row anywhere else; this is the only place it's shown."""
        exe = Execution.model_validate_json(obj.data)
        store = self._store(obj)
        parts: list[str] = []
        if exe.parent_id:
            parts.append(format_html("<p>Parent: {}</p>", _execution_link(exe.parent_id)))
        nodes, overflow = inspection.execution_tree(obj.id, store)
        if len(nodes) > 1 or overflow:
            rows = [
                (
                    format_html(
                        '<span class="harel-tree-id" style="padding-left: {}em">{}</span>',
                        n.depth * 1.5,
                        _execution_link(n.id),
                    ),
                    n.kind if n.depth else "—",
                    n.status,
                    n.active_path or "—",
                    n.outcome or "—",
                )
                for n in nodes
            ]
            parts.append(_table(("Execution", "Kind", "Status", "State", "Outcome"), rows, more=overflow))
        if not parts:
            return format_html('<span class="harel-muted">{}</span>', "— (no children)")
        return format_html_join("", "{}", ((p,) for p in parts))

    @admin.display(description="waiting")
    def pending(self, obj):
        using = obj._state.db or "default"
        events = inspection.pending_events(obj.id, using, limit=_MAX_ROWS)
        timers = inspection.armed_timers(obj.id, using)
        parts: list[str] = []
        if events:
            parts.append(
                _table(
                    ("Queued event", "Id", "Data", "Held by", "Until"),
                    [
                        (
                            format_html("<code>{}</code>", e.event.kind),
                            e.event.id,
                            _compact(e.event.data),
                            e.held_by or "—",
                            _when(e.held_until),
                        )
                        for e in events
                    ],
                )
            )
        if timers:
            parts.append(
                _table(
                    ("Timer", "Fires at", ""),
                    [
                        (
                            format_html("<code>{}</code>", t.path or "(machine)"),
                            _when(t.fire_at),
                            format_html(
                                '<span class="harel-alert">{}</span>', "overdue — is a worker running?"
                            )
                            if t.overdue
                            else "",
                        )
                        for t in timers
                    ],
                )
            )
        if not parts:
            return format_html('<span class="harel-muted">{}</span>', "— (no queued event, no armed timer)")
        return format_html_join("", "{}", ((p,) for p in parts))

    @admin.display(description="timeline")
    def timeline(self, obj):
        steps = self._store(obj).read_trace(obj.id)
        if not steps:
            return format_html(
                '<span class="harel-muted">{}</span>', "— (recorded with DJANGO_HAREL['TRACE'] = True)"
            )
        rows = [
            (
                _when(step.get("timestamp")),
                format_html("<code>{}</code>", step.get("event_kind")),
                _compact(step.get("event_data") or {}),
                format_html("{} → {}", step.get("from_path") or "(start)", step.get("to_path") or "—"),
                format_html_join(", ", "<code>{}</code>", ((a,) for a in step.get("actions") or ())) or "—",
                _compact(step["assigned"]) if step.get("assigned") else "",
            )
            for step in reversed(steps)  # the latest first
        ]
        return _table(("When", "Event", "Data", "Transition", "Actions", "Set"), rows)

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


@admin.register(HarelMachine)
class HarelMachineAdmin(_DrawsDiagrams, admin.ModelAdmin):
    """The machines catalog: **Harel Statecharts › Machines**, next to **Executions** in the
    sidebar — a `.stm` definition is a different thing from a running instance of one, and
    having both as their own entry, side by side, says so without a word of explanation.

    A proxy of `HarelExecution` purely to get that sidebar entry (see `HarelMachine`); nothing
    here is a real "object" — `changelist_view` is the catalog, and the one custom URL is one
    machine's own page. Permissions mirror `HarelExecutionAdmin`'s, not a separate set."""

    def has_module_permission(self, request):
        return request.user.has_perm("django_harel.view_harelexecution")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("django_harel.view_harelexecution")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        urls = [
            path(
                "<str:definition_id>/",
                self.admin_site.admin_view(self.machine_detail_view),
                name="django_harel_harelmachine_detail",
            )
        ]
        return urls + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        """One row per machine `conf.definitions()` knows (see `inspection.dashboard`): how
        many executions in each status, its failed ones linked, how big its queue is and how
        long the oldest message has waited, and timers overdue — the signal that no worker is
        running. Each name links to the machine's own page."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        using = conf.get_setting("DATABASE")
        defs = conf.definitions()
        rows = [
            _machine_row(summary, defs[summary.definition_id].version)
            for summary in inspection.dashboard(using)
        ]
        context = {
            **self.admin_site.each_context(request),
            "title": "Machines",
            "opts": self.model._meta,
            "rows": rows,
        }
        return TemplateResponse(request, "admin/django_harel/machines.html", context)

    def machine_detail_view(self, request, definition_id: str):
        """One machine: its diagram, what it invokes and which of its states are orthogonal
        (`inspection.structural_facts`), the same operational row `changelist_view` shows, and
        its top-level units (`inspection.top_level_units`) — never a bare orthogonal region,
        which only makes sense inside its unit's own tree (the execution detail page)."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        defn = conf.definitions().get(definition_id)
        if defn is None:
            raise Http404(f"no machine {definition_id!r} in DJANGO_HAREL['MACHINES']")
        using = conf.get_setting("DATABASE")
        units, more = inspection.top_level_units(definition_id, using)
        context = {
            **self.admin_site.each_context(request),
            "title": definition_id,
            "opts": self.model._meta,
            "definition_id": definition_id,
            "diagram": diagram_source(defn, []),
            "facts": _structural_facts_row(defn),
            "row": _machine_row(inspection.machine_summary(definition_id, using), defn.version),
            "units": [_unit_row(u) for u in units],
            "units_more": more,
            "media": self.media,  # Mermaid/admin.js/admin.css: the change form gets this for free
        }
        return TemplateResponse(request, "admin/django_harel/machine_detail.html", context)


def _redrive_targets(defn, executions) -> list[str]:
    """The states every one of `executions` can be redriven to: leaves of its own branch,
    outside any orthogonal state — harel's rule — and not a terminal, which would end it."""
    return sorted(
        node.full_path
        for node in defn.index.values()
        if node.outcome is None
        and all(engine.is_valid_reposition_target(defn, e, node.full_path) for e in executions)
    )


def _changelist_url(**filters) -> str:
    url = reverse("admin:django_harel_harelexecution_changelist")
    if filters:
        from urllib.parse import urlencode

        url += "?" + urlencode(filters)
    return url


def _humanize_age(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _machine_row(summary, version: int) -> dict:
    """A `MachineSummary` as the Machines templates render it: counts linked to the filtered
    changelist, failed executions linked individually, the queue's age humanized, a link to
    the machine's own page, and the machine's own declared version (`machine m version N`)."""
    counts = [
        {
            "status": status,
            "count": count,
            "url": _changelist_url(definition_id=summary.definition_id, status=status) if count else None,
        }
        for status, count in summary.counts.items()
        if count or status in ("RUNNING", "FAILED")  # always shown: what you come here to check
    ]
    return {
        "definition_id": summary.definition_id,
        "detail_url": reverse("admin:django_harel_harelmachine_detail", args=[summary.definition_id]),
        "version": version,
        "total": sum(summary.counts.values()),
        "counts": counts,
        "failed_links": [_execution_link(i) for i in summary.failed_ids],
        "failed_more": summary.failed_more,
        "failed_url": _changelist_url(definition_id=summary.definition_id, status="FAILED"),
        "queued": summary.queued,
        "oldest_queued_age": _humanize_age(summary.oldest_queued_age) if summary.oldest_queued_age else None,
        "overdue_timers": summary.overdue_timers,
    }


def _structural_facts_row(defn) -> dict:
    """A `StructuralFacts` as the machine detail template renders it: each invoke's target
    linked to its own machine page when `conf.definitions()` knows it (a submachine does)."""
    facts = inspection.structural_facts(defn)
    known = conf.definitions()
    return {
        "invokes": [
            {
                "state": state,
                "target": target,
                "url": reverse("admin:django_harel_harelmachine_detail", args=[target])
                if target in known
                else None,
            }
            for state, target in facts.invokes
        ],
        "orthogonal": [{"state": state, "regions": regions} for state, regions in facts.orthogonal],
    }


def _unit_row(unit) -> dict:
    """A `TopLevelUnit` as the machine detail template renders it."""
    return {
        "link": _execution_link(unit.id),
        "status": unit.status,
        "active_path": unit.active_path or "—",
        "outcome": unit.outcome or "—",
        "failed_descendants": unit.failed_descendants,
    }
