"""manage.py harel_worker — run the events the requests queued, and the timers that fall due.

One process runs one event at a time, in its own thread, on Django's connection: its actions
use the ORM like any Django code. Run more processes to run more events at once; each
execution's events still run one at a time, in order. SIGTERM or Ctrl-C stops it after the
event in hand.

With `DJANGO_HAREL['ATOMIC_STEP']` (the default) each event is one transaction: the claim,
what its actions write to the database, the machine's step and the ack commit together, or
none of them does — a worker that dies mid-event leaves nothing behind, and the event is
claimable again at once.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
from contextlib import nullcontext

from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS, close_old_connections, transaction

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

    def handle(self, *args, **options):
        worker_id = options["worker_id"] or f"{socket.gethostname()}:{os.getpid()}"
        visibility = options["visibility"] or conf.get_setting("VISIBILITY")
        idle_sleep = options["idle_sleep"] or conf.get_setting("IDLE_SLEEP")

        definitions = conf.definitions()
        worker = conf.distributed_runner().worker(
            worker_id=worker_id,
            visibility=visibility,
            suspend_recheck=conf.get_setting("SUSPEND_RECHECK"),
        )

        stop = threading.Event()
        previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
        self.stdout.write(f"harel worker {worker_id}: {', '.join(sorted(definitions))}")
        try:
            run(
                worker,
                stop,
                idle_sleep,
                atomic=conf.get_setting("ATOMIC_STEP"),
                using=conf.get_setting("DATABASE"),
            )
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        self.stdout.write("harel worker stopped")


def run(
    worker, stop: threading.Event, idle_sleep: float, *, atomic: bool = True, using: str = DEFAULT_DB_ALIAS
) -> None:
    """Claim and run events until `stop`; fire the due timers when the queue is empty. With
    `atomic`, each event (and each timer sweep) is one transaction on the database `using`."""

    def unit():
        return transaction.atomic(using=using) if atomic else nullcontext()

    while not stop.is_set():
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
    close_old_connections()
