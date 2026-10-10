"""Load test: N executions through W `harel_worker` processes, checked for exactly-once.

    HAREL_TEST_DB=postgres uv run python bench/run.py --executions 2000 --workers 4

Workers start first; then the executions are created and each is sent its `Go` while the
workers run — so publishes race the workers' acks on the same executions. At the end every
execution must be DONE, every action must have run exactly once (`Tick` has a unique key per
execution and step: a second run would fail the step), and the queue and the outbox must be
empty. Exits non-zero if anything is off.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.settings")

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from django.db import connection, transaction  # noqa: E402
from harel.spec.states import Event  # noqa: E402

from django_harel import distributed_runner  # noqa: E402
from django_harel.schema import ALL_TABLES  # noqa: E402
from tests.app.models import Tick  # noqa: E402


def _count(sql: str) -> int:
    with connection.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def _done() -> int:
    return _count('SELECT COUNT(*) FROM harel_executions WHERE data LIKE \'%"status":"DONE"%\'')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executions", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--producers", type=int, default=2, help="threads creating and sending")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument(
        "--prefill",
        action="store_true",
        help="queue everything before the workers start (measures the workers alone)",
    )
    args = parser.parse_args()
    n = args.executions

    call_command("migrate", verbosity=0)
    with connection.cursor() as cur:
        for table in ALL_TABLES:
            cur.execute(f"DELETE FROM {table}")
    Tick.objects.all().delete()
    connection.close()

    runner = distributed_runner()

    def produce(ids: range) -> None:
        try:
            for i in ids:
                with transaction.atomic():
                    exe = runner.create("pipeline", context={"id": f"p{i}"}, execution_id=f"p{i}")
                with transaction.atomic():
                    runner.send(exe.id, Event(kind="Go"))
        finally:
            connection.close()

    def produce_all() -> None:
        step = -(-n // args.producers)
        threads = [
            threading.Thread(target=produce, args=(range(k, min(k + step, n)),)) for k in range(0, n, step)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    if args.prefill:
        produce_all()

    logs = ROOT / "bench" / "logs"
    logs.mkdir(exist_ok=True)
    workers = [
        subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "bench" / "manage.py"),
                "harel_worker",
                "--worker-id",
                f"w{i}",
                "--idle-sleep",
                "0.02",
            ],
            stdout=open(logs / f"w{i}.log", "w"),
            stderr=subprocess.STDOUT,
        )
        for i in range(args.workers)
    ]
    if args.prefill:
        # time from the first claim: wait until the workers have started
        while _count("SELECT COUNT(*) FROM harel_transport_messages") == 2 * n:
            time.sleep(0.01)
    else:
        time.sleep(2)  # let them start
    started = time.monotonic()
    if not args.prefill:
        produce_all()
    produced = time.monotonic() - started

    deadline = started + args.timeout
    while _done() < n and time.monotonic() < deadline:
        time.sleep(0.2)
    elapsed = time.monotonic() - started

    for w in workers:
        w.send_signal(signal.SIGTERM)
    exits = [w.wait(30) for w in workers]

    done = _done()
    ticks = Tick.objects.count()
    queued = _count("SELECT COUNT(*) FROM harel_transport_messages")
    groups = _count("SELECT COUNT(*) FROM harel_transport_groups")
    outbox = _count("SELECT COUNT(*) FROM harel_outbox")
    tracebacks = sum((logs / f"w{i}.log").read_text().count("Traceback") for i in range(args.workers))

    print(
        f"db={connection.vendor} executions={n} workers={args.workers} producers={args.producers} prefill={args.prefill}"
    )
    print(f"produced in {produced:.1f}s; all done in {elapsed:.1f}s")
    print(f"throughput: {2 * n / elapsed:.0f} events/s, {3 * n / elapsed:.0f} actions/s")
    print(f"done={done}/{n} ticks={ticks}/{3 * n} queued={queued} groups={groups} outbox={outbox}")
    print(f"worker exits={exits} tracebacks in logs={tracebacks}")

    ok = done == n and ticks == 3 * n and queued == groups == outbox == 0 and exits == [0] * len(workers)
    print("OK" if ok and not tracebacks else "FAILED")
    return 0 if ok and not tracebacks else 1


if __name__ == "__main__":
    sys.exit(main())
