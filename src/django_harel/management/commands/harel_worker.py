"""manage.py harel_worker — run the events the requests queued, and the timers that fall due.

One process runs one event at a time, in its own thread, on Django's connection: its actions
use the ORM like any Django code. Run more processes to run more events at once; each
execution's events still run one at a time, in order. SIGTERM or Ctrl-C stops it after the
event in hand.

With `DJANGO_HAREL['ATOMIC_STEP']` (the default) each event is one transaction: the claim,
what its actions write to the database, the machine's step and the ack commit together, or
none of them does — a worker that dies mid-event leaves nothing behind, and the event is
claimable again at once.

Before it claims anything, the worker publishes the version of every machine in
`DJANGO_HAREL['MACHINES']` — like a native harel worker does. A `.stm` edited without a new
`version` is caught here (`CommandError`), before any execution meets the mismatch.

While it runs, the worker records a heartbeat in `harel_workers` every
`DJANGO_HAREL['HEARTBEAT']` seconds — its machines, when it started, when it was last seen —
and removes it when it stops, as a native harel worker does: harel's monitor reads it to tell
which machines no live worker covers.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from contextlib import nullcontext
from typing import Any, Callable, Optional

from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, close_old_connections, transaction
from harel.engine.store.base import WorkerRecord
from harel.published import DefinitionConflict

from django_harel import conf

logger = logging.getLogger("django_harel.worker")


class Command(BaseCommand):
    help = "Run harel's queued events and due timers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--worker-id", default=None, help="This worker's name in its leases (default: host:pid)."
        )
        parser.add_argument(
            "--visibility",
            type=float,
            default=None,
            help="Seconds a claimed event stays leased before another worker may take it "
            "(default: DJANGO_HAREL['VISIBILITY'], 30). Longer than your slowest action.",
        )
        parser.add_argument(
            "--idle-sleep",
            type=float,
            default=None,
            help="Seconds to wait when there's nothing to do (default: DJANGO_HAREL['IDLE_SLEEP'], 0.2).",
        )
        parser.add_argument(
            "--machines",
            default=None,
            help="Comma-separated machines: this worker claims only the work of the executions "
            "started from them (their whole tree, invoked submachines included). Unset: every "
            "machine, the safety net a dedicated worker's capacity sits alongside.",
        )

    def handle(self, *args, **options):
        worker_id = options["worker_id"] or f"{socket.gethostname()}:{os.getpid()}"
        visibility = options["visibility"] or conf.get_setting("VISIBILITY")
        idle_sleep = options["idle_sleep"] or conf.get_setting("IDLE_SLEEP")

        definitions = conf.definitions()
        machines = _parse_machines(options["machines"], definitions)
        atomic = conf.get_setting("ATOMIC_STEP")
        runner = conf.worker_runner(atomic_step=atomic)
        try:
            conf.publish_machine_definitions(runner.store)
        except DefinitionConflict as exc:
            raise CommandError(str(exc)) from exc
        worker = runner.worker(
            worker_id=worker_id,
            visibility=visibility,
            suspend_recheck=conf.get_setting("SUSPEND_RECHECK"),
            machines=machines,
        )

        heartbeat = Heartbeat(runner.store, worker_id, machines, conf.get_setting("HEARTBEAT"))

        stop = threading.Event()
        previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
        serves = ", ".join(sorted(machines)) if machines is not None else ", ".join(sorted(definitions))
        self.stdout.write(f"harel worker {worker_id}: {serves}")
        try:
            run(
                worker,
                stop,
                idle_sleep,
                atomic=atomic,
                using=conf.get_setting("DATABASE"),
                heartbeat=heartbeat,
            )
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        self.stdout.write("harel worker stopped")


def _parse_machines(raw: str | None, definitions: dict) -> frozenset[str] | None:
    """`--machines`, as the set `Worker(machines=...)` takes: `None` (unset) claims every
    machine; a name not in `DJANGO_HAREL['MACHINES']` is almost always a typo, caught here
    rather than as a worker that silently never claims anything."""
    if raw is None:
        return None
    machines = frozenset(name.strip() for name in raw.split(",") if name.strip())
    unknown = sorted(machines - set(definitions))
    if unknown:
        raise CommandError(
            f"--machines names a machine not in DJANGO_HAREL['MACHINES']: {', '.join(unknown)}"
        )
    return machines


class Heartbeat:
    """The worker's heartbeat in the store: `beat()` records it when one is due — at the first
    call, then every `interval` seconds — and `forget()` removes it. A heartbeat that fails is
    logged and tried again a whole interval later: the worker runs on without it."""

    def __init__(
        self,
        store: Any,
        worker_id: str,
        machines: Optional[frozenset[str]],
        interval: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store, self.worker_id, self.interval, self._clock = store, worker_id, interval, clock
        self.machines = tuple(sorted(machines)) if machines is not None else None
        self._started_at: Optional[float] = None
        self._next = 0.0

    def beat(self) -> None:
        now = self._clock()
        if now < self._next:
            return
        if self._started_at is None:
            self._started_at = now
        self._next = now + self.interval
        try:
            self.store.record_worker(
                WorkerRecord(self.worker_id, self.machines, self._started_at, now, self.interval)
            )
        except Exception:
            logger.exception("harel worker %s could not record its heartbeat", self.worker_id)

    def forget(self) -> None:
        try:
            self.store.forget_worker(self.worker_id)
        except Exception:
            logger.exception("harel worker %s could not forget its heartbeat", self.worker_id)


def run(
    worker,
    stop: threading.Event,
    idle_sleep: float,
    *,
    atomic: bool = True,
    using: str = DEFAULT_DB_ALIAS,
    heartbeat: Optional[Heartbeat] = None,
) -> None:
    """Claim and run events until `stop`; fire the due timers when the queue is empty. With
    `atomic`, each event (and each timer sweep) is one transaction on the database `using`.
    `heartbeat`, when given, is recorded between events and forgotten when the loop ends."""

    def unit():
        return transaction.atomic(using=using) if atomic else nullcontext()

    try:
        while not stop.is_set():
            if heartbeat is not None:
                heartbeat.beat()  # outside the event's transaction: a heartbeat is no part of it
            try:
                with unit():
                    progressed = worker.step()
                if progressed:
                    continue  # keep the connection while there's work: no reconnect per event
                with unit():
                    fired = worker.fire_due_timers()
                if fired:
                    continue
            except Exception:
                # the store or the queue failed (the database went away?), and the event in hand
                # comes back: at once if its step rolled back, else when its lease ends — wait,
                # then carry on
                logger.exception("harel worker iteration failed")
            # idle, or after a failure: drop a connection that broke or outlived CONN_MAX_AGE
            # (the worker lives for days); the next query opens a fresh one
            close_old_connections()
            stop.wait(idle_sleep)
    finally:
        if heartbeat is not None:
            heartbeat.forget()
        close_old_connections()
