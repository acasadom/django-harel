"""What the admin shows about an execution beyond its own record: its machine, the states it is
in, the events waiting in its queue and its armed timers. Read-only."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from django.db import connections
from harel.definition.model import Definition
from harel.engine.execution import Execution
from harel.engine.transport.base import PARKED
from harel.spec.states import Event

from django_harel import conf
from django_harel.store import DjangoStore
from django_harel.transport import GROUPS, MESSAGES


def machine_for(exe: Execution) -> Optional[Definition]:
    """The machine `exe` runs: its own, an orthogonal region's (its parent's machine), or the
    submachine an `invoke` started. None for one not in `DJANGO_HAREL['MACHINES']`."""
    conf.distributed_runner()  # builds the runner, which registers every submachine by id
    return conf.definitions().get(exe.definition_id)


def active_paths(exe: Execution, store: DjangoStore) -> list[str]:
    """The states `exe` is in: its active state, and inside an orthogonal state, the one each
    live region is in (a region is a child execution of the same machine)."""
    paths = [exe.active_path] if exe.active_path else []
    for child_id, child in exe.children.items():
        if child.finished or child.submachine:
            continue
        region = store.load(child_id)
        if region is not None and region.active_path:
            paths.extend(active_paths(region, store))
    return paths


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
