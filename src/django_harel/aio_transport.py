"""AsyncDjangoTransport — a thin async wrapper over DjangoTransport.

Same design as AsyncDjangoStore: delegates to the sync DjangoTransport via
`sync_to_async`. Compatible with `harel.engine.aio.distributed.AsyncWorker`.
"""

from __future__ import annotations

from typing import Any, Optional

from asgiref.sync import sync_to_async
from harel.engine.transport import Lease
from harel.spec.states import Event


class AsyncDjangoTransport:
    """AsyncTransport that wraps any sync Transport via sync_to_async.

    Accepts any object implementing the harel Transport protocol (DjangoTransport,
    RedisTransport, PostgresTransport, …). Defaults to DjangoTransport when no
    transport is supplied."""

    def __init__(self, transport: Optional[Any] = None) -> None:
        if transport is None:
            from django_harel.transport import DjangoTransport

            transport = DjangoTransport()
        self._transport = transport

    async def publish(self, group_id: str, event: Event) -> None:
        return await sync_to_async(self._transport.publish)(group_id, event)

    async def claim(self, worker_id: str, visibility: float) -> Optional[Lease]:
        return await sync_to_async(self._transport.claim)(worker_id, visibility)

    async def ack(self, lease: Lease) -> None:
        return await sync_to_async(self._transport.ack)(lease)

    async def nack(self, lease: Lease, delay: float = 0.0) -> None:
        return await sync_to_async(self._transport.nack)(lease, delay)

    async def close(self) -> None:
        pass
