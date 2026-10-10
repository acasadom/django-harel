"""What the admin shows about an execution beyond its own record: its machine, the states it is
in, the events waiting in its queue and its armed timers. Read-only."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from django.db import connections
from harel.definition.model import Definition
from harel.engine.execution import Execution, Status
from harel.engine.transport.base import PARKED
from harel.spec.states import Event

from django_harel import conf
from django_harel.schema import QUEUE_LOG
from django_harel.store import DjangoStore, json_field
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
            f"SELECT COUNT(*), MIN(p.published_at) FROM {MESSAGES} m "
            f"JOIN harel_executions e ON e.id = m.group_id "
            f"LEFT JOIN {QUEUE_LOG} p ON p.seq = m.seq "
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
