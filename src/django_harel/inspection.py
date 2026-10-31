"""What the admin shows about an execution beyond its own record: its machine, the states it is
in, the events waiting in its queue and its armed timers. Read-only."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from django.db import connections
from harel.definition.model import Definition, NodeKind
from harel.engine.execution import Execution, Status
from harel.engine.transport.base import PARKED
from harel.spec.states import Event

from django_harel import conf
from django_harel.store import DjangoStore, json_field, like_prefix
from django_harel.transport import GROUPS, MESSAGES


def machine_for(exe: Execution) -> Optional[Definition]:
    """The machine `exe` runs: its own, an orthogonal region's (its parent's machine), or the
    submachine an `invoke` started (`conf.definitions()` registers those too). None for one not
    reachable from `DJANGO_HAREL['MACHINES']`."""
    return conf.definitions().get(exe.definition_id)


def active_paths(exe: Execution, store: DjangoStore) -> list[str]:
    """The states `exe` is in: its active state — or, parked at an orthogonal state, the states
    its live regions are in (each region is a child execution of the same machine), which say
    more than the orthogonal state containing them all."""
    regions = []
    for child_id, child in exe.children.items():
        if child.finished or child.submachine:
            continue
        region = store.load(child_id)
        if region is not None and region.active_path:
            regions.extend(active_paths(region, store))
    if regions:
        return regions
    return [exe.active_path] if exe.active_path else []


@dataclass
class PendingEvent:
    seq: int
    event: Event
    held_by: Optional[str]  # the worker leasing it, or "parked" for one a suspension parked
    held_until: Optional[float]


def pending_events(execution_id: str, using: str = "default", limit: int = 50) -> list[PendingEvent]:
    """The events in `execution_id`'s queue, oldest first, with the lease on the one in hand."""
    connection = connections[using]
    with connection.cursor() as cur:
        if connection.vendor == "sqlite":  # the lease is on the message
            cur.execute(
                f"SELECT seq, event, locked_by, lock_expiry FROM {MESSAGES} WHERE group_id = %s ORDER BY seq LIMIT %s",
                [execution_id, limit],
            )
            rows = cur.fetchall()
        else:  # the lease is on the group, and covers its head
            cur.execute(
                f"SELECT seq, event FROM {MESSAGES} WHERE group_id = %s ORDER BY seq LIMIT %s",
                [execution_id, limit],
            )
            messages = cur.fetchall()
            cur.execute(f"SELECT locked_by, lock_expiry FROM {GROUPS} WHERE group_id = %s", [execution_id])
            lease = cur.fetchone() or (None, None)
            rows = [
                (seq, event, *(lease if i == 0 else (None, None))) for i, (seq, event) in enumerate(messages)
            ]
    now = time.time()
    pending = []
    for seq, payload, held_by, held_until in rows:
        if held_until is not None and held_until < now:
            held_by, held_until = None, None  # an ended lease holds nothing
        if held_by == PARKED:
            held_by = "parked"
        pending.append(PendingEvent(seq, Event.model_validate_json(payload), held_by, held_until))
    return pending


# a due timer fires when a worker next finds its queue empty: one that waits longer than this
# says no worker is sweeping
OVERDUE_AFTER = 60.0


@dataclass
class ArmedTimer:
    path: str
    fire_at: float

    @property
    def overdue(self) -> bool:
        """Due a while ago and not fired: no worker has swept the timers since."""
        return time.time() - self.fire_at > OVERDUE_AFTER


def armed_timers(execution_id: str, using: str = "default") -> list[ArmedTimer]:
    with connections[using].cursor() as cur:
        cur.execute(
            "SELECT path, fire_at FROM harel_timers WHERE execution_id = %s ORDER BY fire_at", [execution_id]
        )
        return [ArmedTimer(path, float(fire_at)) for path, fire_at in cur.fetchall()]


# --- the dashboard: one row per machine --------------------------------------------------------


@dataclass
class MachineSummary:
    """One machine's row on the dashboard."""

    definition_id: str
    counts: dict[str, int]  # Status.value -> count, every status present (0 if none)
    failed_ids: list[str]  # oldest first, up to _MAX_FAILED
    failed_more: int  # how many more FAILED beyond failed_ids
    queued: int  # events waiting across this machine's executions
    oldest_queued_age: Optional[float]  # seconds since the oldest was published; None if queued == 0
    overdue_timers: int  # armed past OVERDUE_AFTER and not yet fired


_MAX_FAILED = 10


def machine_summary(definition_id: str, using: str = "default") -> MachineSummary:
    connection = connections[using]
    status_col = json_field(connection.vendor, "status")
    now = time.time()

    with connection.cursor() as cur:
        cur.execute(
            f"SELECT {status_col}, COUNT(*) FROM harel_executions WHERE definition_id = %s GROUP BY {status_col}",
            [definition_id],
        )
        counts = {s.value: 0 for s in Status}
        for status, count in cur.fetchall():
            counts[status] = count
        total_failed = counts[Status.FAILED.value]

        cur.execute(
            "SELECT id FROM harel_executions WHERE definition_id = %s "
            f"AND {status_col} = %s ORDER BY id LIMIT %s",
            [definition_id, Status.FAILED.value, _MAX_FAILED],
        )
        failed_ids = [r[0] for r in cur.fetchall()]

        cur.execute(
            f"SELECT COUNT(*), MIN(m.published_at) FROM {MESSAGES} m "
            f"JOIN harel_executions e ON e.id = m.group_id "
            "WHERE e.definition_id = %s",
            [definition_id],
        )
        queued, oldest_published = cur.fetchone()
        oldest_age = now - oldest_published if oldest_published is not None else None

        cur.execute(
            "SELECT COUNT(*) FROM harel_timers t JOIN harel_executions e ON e.id = t.execution_id "
            "WHERE e.definition_id = %s AND t.fire_at < %s",
            [definition_id, now - OVERDUE_AFTER],
        )
        overdue_timers = cur.fetchone()[0]

    return MachineSummary(
        definition_id=definition_id,
        counts=counts,
        failed_ids=failed_ids,
        failed_more=max(0, total_failed - len(failed_ids)),
        queued=queued or 0,
        oldest_queued_age=oldest_age,
        overdue_timers=overdue_timers,
    )


def dashboard(using: str = "default") -> list[MachineSummary]:
    """One `MachineSummary` per machine `conf.definitions()` knows: every entry in
    `DJANGO_HAREL['MACHINES']`, and the submachines they `invoke` — a fan-out's parcel, say —
    since those run as their own executions under their own id too."""
    return [machine_summary(definition_id, using) for definition_id in conf.definitions()]


# --- a machine's shape, independent of any execution --------------------------------------------


@dataclass
class StructuralFacts:
    """What a `Definition` is shaped like: where it invokes other machines, and which of its
    states are orthogonal (fork into regions) — static, the same for every execution of it."""

    invokes: list[tuple[str, str]]  # (state's full_path, target FQN) — one per `invoke`, fan-out included
    orthogonal: list[tuple[str, list[str]]]  # (state's full_path, [region names]) — one per AND-state


def structural_facts(defn: Definition) -> StructuralFacts:
    invokes = sorted((node.full_path, str(node.invoke)) for node in defn.index.values() if node.invoke)
    orthogonal = sorted(
        (node.full_path, [child.name for child in node.children])
        for node in defn.index.values()
        if node.kind is NodeKind.ORTHOGONAL
    )
    return StructuralFacts(invokes=invokes, orthogonal=orthogonal)


# --- a machine's own top-level units --------------------------------------------------------


@dataclass
class TopLevelUnit:
    """One execution worth listing on its own — never an orthogonal region (see
    `top_level_units`). `failed_descendants` rolls up its whole subtree, so a failure inside a
    region or an invoked child isn't hidden behind a root whose own status still reads RUNNING."""

    id: str
    status: str
    active_path: Optional[str]
    outcome: Optional[str]
    failed_descendants: int


_MAX_UNITS = 50


def top_level_units(definition_id: str, using: str = "default") -> tuple[list[TopLevelUnit], int]:
    """The executions of `definition_id` worth listing as their own thing: its roots, and any
    invoked submachine's own entry point (a child whose `definition_id` differs from its
    parent's). An orthogonal region (same `definition_id` as its parent) never stands alone —
    it only makes sense nested inside its parent's tree (`execution_tree`).

    Oldest first; the first `_MAX_UNITS` and how many more there are."""
    connection = connections[using]
    status_col = json_field(connection.vendor, "status")
    active_col = json_field(connection.vendor, "active_path")
    outcome_col = json_field(connection.vendor, "outcome")
    parent_col = json_field(connection.vendor, "parent_id")

    with connection.cursor() as cur:
        cur.execute(
            f"SELECT id, {status_col}, {active_col}, {outcome_col}, {parent_col} "
            "FROM harel_executions WHERE definition_id = %s ORDER BY id",
            [definition_id],
        )
        rows = cur.fetchall()
        parent_ids = {r[4] for r in rows if r[4] is not None}
        parent_machine: dict[str, str] = {}
        if parent_ids:
            placeholders = ", ".join(["%s"] * len(parent_ids))
            cur.execute(
                f"SELECT id, definition_id FROM harel_executions WHERE id IN ({placeholders})",
                list(parent_ids),
            )
            parent_machine = dict(cur.fetchall())

    units = [
        (exe_id, status, active_path, outcome)
        for exe_id, status, active_path, outcome, parent_id in rows
        if parent_id is None or parent_machine.get(parent_id) != definition_id
    ]
    total = len(units)
    kept = units[:_MAX_UNITS]

    # MySQL's LIKE escapes with a backslash by default (an explicit ESCAPE clause there would
    # double up); see DjangoStore.ids_with_prefix, the same pattern
    escape = "" if connection.vendor == "mysql" else " ESCAPE '\\'"

    def failed_descendants(unit_id: str) -> int:
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM harel_executions WHERE id LIKE %s{escape} AND {status_col} = %s",
                [like_prefix(f"{unit_id}:"), Status.FAILED.value],
            )
            return cur.fetchone()[0]

    return [
        TopLevelUnit(exe_id, status, active_path, outcome, failed_descendants(exe_id))
        for exe_id, status, active_path, outcome in kept
    ], max(0, total - len(kept))


# --- one unit's whole tree -------------------------------------------------------------------


@dataclass
class TreeNode:
    id: str
    depth: int  # 0 = the unit itself
    kind: str  # "unit" | "region" | "invoke"
    status: str
    active_path: Optional[str]
    outcome: Optional[str]


_MAX_TREE_NODES = 200


def execution_tree(
    execution_id: str, store: DjangoStore, max_nodes: int = _MAX_TREE_NODES
) -> tuple[list[TreeNode], int]:
    """`execution_id` and everything hanging off it — regions, invoked children, and theirs —
    depth-first. The first `max_nodes` and how many more there are."""
    root = store.load(execution_id)
    if root is None:
        return [], 0
    nodes = [TreeNode(root.id, 0, "unit", root.status.value, root.active_path, root.outcome)]
    overflow = 0

    def walk(exe: Execution, depth: int) -> None:
        nonlocal overflow
        for child_id, child in exe.children.items():
            loaded = store.load(child_id)
            if loaded is None:
                continue
            if len(nodes) >= max_nodes:
                overflow += 1
                continue
            nodes.append(
                TreeNode(
                    loaded.id,
                    depth,
                    "invoke" if child.submachine else "region",
                    loaded.status.value,
                    loaded.active_path,
                    loaded.outcome,
                )
            )
            walk(loaded, depth + 1)

    walk(root, 1)
    return nodes, overflow
