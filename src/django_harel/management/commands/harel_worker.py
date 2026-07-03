"""manage.py harel_worker — run a harel background worker over Django's database.

Loads definitions from DJANGO_HAREL["MACHINES"] (a dict of
``definition_id → path/to/file.stm``) and runs an AsyncWorker with the
Django-backed store + transport. Press Ctrl-C to stop.

Example settings::

    DJANGO_HAREL = {
        "MACHINES": {
            "checkout": "myapp/machines/checkout.stm",
            "onboarding": "myapp/machines/onboarding.stm",
        },
        "CONCURRENCY": 8,
        "VISIBILITY": 30.0,
    }
"""

from __future__ import annotations

import socket

import anyio
from django.core.management.base import BaseCommand

from django_harel.aio_store import AsyncDjangoStore
from django_harel.aio_transport import AsyncDjangoTransport
from django_harel.conf import build_transport, get_harel_setting


class Command(BaseCommand):
    help = "Run a harel background worker using the Django database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--concurrency",
            type=int,
            default=None,
            help="Max events in flight simultaneously (default: DJANGO_HAREL['CONCURRENCY'] or 8).",
        )
        parser.add_argument(
            "--visibility",
            type=float,
            default=None,
            help="Lease visibility window in seconds (default: DJANGO_HAREL['VISIBILITY'] or 30).",
        )
        parser.add_argument(
            "--worker-id",
            default=None,
            help="Unique worker identifier (default: hostname).",
        )
        parser.add_argument(
            "--trace",
            action="store_true",
            default=False,
            help="Enable per-execution trace recording.",
        )

    def handle(self, *args, **options):
        from harel.dsl import definition_from_dsl_file
        from harel.engine.aio.distributed import AsyncWorker

        machines: dict[str, str] = get_harel_setting("MACHINES", {})
        if not machines:
            self.stderr.write(
                self.style.ERROR("No machines configured. Add DJANGO_HAREL['MACHINES'] to settings.")
            )
            return

        concurrency: int = options["concurrency"] or get_harel_setting("CONCURRENCY", 8)
        visibility: float = options["visibility"] or get_harel_setting("VISIBILITY", 30.0)
        worker_id: str = options["worker_id"] or get_harel_setting("WORKER_ID", socket.gethostname())

        definitions = {}
        for definition_id, path in machines.items():
            defn = definition_from_dsl_file(path)
            definitions[definition_id] = defn
            self.stdout.write(f"  Loaded definition: {definition_id} from {path}")

        store = AsyncDjangoStore()
        transport = AsyncDjangoTransport(build_transport())
        worker = AsyncWorker(
            store=store,
            transport=transport,
            definitions=definitions,
            worker_id=worker_id,
            visibility=visibility,
            concurrency=concurrency,
            trace=options["trace"],
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Starting harel worker [{worker_id}] concurrency={concurrency} visibility={visibility}s"
            )
        )

        async def _run():
            import asyncio
            import signal

            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)
            try:
                await worker.run(stop)
            finally:
                await store.close()
                await transport.close()

        try:
            anyio.run(_run)
        except KeyboardInterrupt:
            pass
        self.stdout.write(self.style.WARNING("Worker stopped."))
