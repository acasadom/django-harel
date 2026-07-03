"""AsyncDjangoStore — a thin async wrapper over DjangoStore.

Wraps every DjangoStore method with `sync_to_async` so the harel async worker
(`AsyncWorker`) can use the same Django ORM-backed store. Django ORM operations
run in a thread pool (Django's default sync-in-async behaviour via asgiref).

The wrapper is intentionally thin: all logic lives in DjangoStore. If you need
raw async ORM performance (Django 4.1+ async queryset API), replace this with a
native async implementation — the `AsyncExecutionStore` protocol is the contract.
"""

from __future__ import annotations

from typing import Optional

from asgiref.sync import sync_to_async
from harel.engine.execution import Execution
from harel.engine.store import OutboxEntry, SpawnEntry, TimerOp
from harel.spec.states import Event

from django_harel.store import DjangoStore


class AsyncDjangoStore:
    """AsyncExecutionStore that delegates to a DjangoStore via sync_to_async.

    Compatible with `harel.engine.aio.distributed.AsyncWorker`."""

    def __init__(self, store: Optional[DjangoStore] = None) -> None:
        self._store = store if store is not None else DjangoStore()

    async def load(self, execution_id: str) -> Optional[Execution]:
        return await sync_to_async(self._store.load)(execution_id)

    async def save(self, exe: Execution) -> None:
        return await sync_to_async(self._store.save)(exe)

    async def commit(
        self,
        exe: Execution,
        emits: list[tuple[Optional[str], Event]],
        processed_event_id: Optional[str] = None,
        timers: tuple[TimerOp, ...] = (),
        spawns: tuple[tuple[str, str, dict], ...] = (),
        trace: Optional[dict] = None,
    ) -> None:
        return await sync_to_async(self._store.commit)(exe, emits, processed_event_id, timers, spawns, trace)

    async def is_processed(self, execution_id: str, event_id: str) -> bool:
        return await sync_to_async(self._store.is_processed)(execution_id, event_id)

    async def pending_outbox(self) -> list[OutboxEntry]:
        return await sync_to_async(self._store.pending_outbox)()

    async def ack_outbox(self, seq: int) -> None:
        return await sync_to_async(self._store.ack_outbox)(seq)

    async def pending_spawns(self) -> list[SpawnEntry]:
        return await sync_to_async(self._store.pending_spawns)()

    async def ack_spawn(self, seq: int) -> None:
        return await sync_to_async(self._store.ack_spawn)(seq)

    async def due_timers(self, now: float) -> list[tuple[str, str, float]]:
        return await sync_to_async(self._store.due_timers)(now)

    async def delete_timer(self, execution_id: str, path: str, fire_at: float) -> None:
        return await sync_to_async(self._store.delete_timer)(execution_id, path, fire_at)

    async def close(self) -> None:
        pass
