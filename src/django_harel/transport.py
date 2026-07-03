"""DjangoTransport — a Transport backed by the harel_queue Django model.

Provides a "zero external dependency" queue for the harel worker: every
message is a row in `harel_queue`, committing and querying via Django's ORM /
raw SQL. Correct under concurrent workers thanks to a compare-and-swap UPDATE.

For high-throughput production deployments prefer the Redis or Postgres
transports from the harel package; DjangoTransport targets development,
single-server, and low-volume production setups where you want a single
database (no Redis) and simple operations (Django migrations, Django admin).
"""

from __future__ import annotations

import time
from typing import Optional

from django.db import connection
from django.db import transaction as djtx
from harel.engine.transport._base import _PARKED, Lease
from harel.spec.states import Event


class DjangoTransport:
    """Transport implementation over the `harel_queue` Django model.

    `publish` INSERTs a row; `claim` atomically locks the oldest message of a
    group that has no in-flight message (single-active-consumer per group);
    `ack` deletes the row; `nack` parks it (delays redelivery).

    The claim uses raw SQL with `%s` placeholders (normalised per backend by
    Django). The CAS UPDATE wraps its self-referential subquery in a derived
    table alias so it runs correctly on SQLite, PostgreSQL, and MySQL/MariaDB."""

    def publish(self, group_id: str, event: Event) -> None:
        from django_harel.models import HarelQueue

        HarelQueue.objects.create(
            group_id=group_id,
            payload=event.model_dump_json(),
            available_at=0.0,
        )

    def claim(self, worker_id: str, visibility: float) -> Optional[Lease]:
        """Find the oldest available message from a group with no in-flight
        message, lock it with `worker_id`, and return a Lease. Returns None if
        nothing is deliverable right now.

        Atomicity: a two-step SELECT + conditional UPDATE. The UPDATE condition
        (`locked_by IS NULL … AND group NOT IN in-flight`) acts as a CAS guard:
        if another worker claimed the group between SELECT and UPDATE the
        rowcount is 0 and we return None (the caller retries on the next tick)."""
        now = time.time()
        expiry = now + visibility
        with djtx.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT seq, group_id, payload FROM harel_queue "
                    "WHERE available_at <= %s "
                    "  AND (locked_by IS NULL OR lock_expiry <= %s) "
                    "  AND group_id NOT IN ("
                    "    SELECT group_id FROM harel_queue "
                    "    WHERE locked_by IS NOT NULL AND lock_expiry > %s"
                    "  ) "
                    "ORDER BY available_at, seq LIMIT 1",
                    [now, now, now],
                )
                row = cur.fetchone()
            if row is None:
                return None
            seq, group_id, payload = row
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE harel_queue SET locked_by = %s, lock_expiry = %s "
                    "WHERE seq = %s "
                    "  AND (locked_by IS NULL OR lock_expiry <= %s) "
                    "  AND group_id NOT IN ("
                    "    SELECT group_id FROM ("
                    "      SELECT g2.group_id FROM harel_queue g2 "
                    "      WHERE g2.locked_by IS NOT NULL AND g2.lock_expiry > %s AND g2.seq != %s"
                    "    ) AS _locked"
                    "  )",
                    [worker_id, expiry, seq, now, now, seq],
                )
                if cur.rowcount == 0:
                    return None
        return Lease(
            seq=seq,
            group_id=group_id,
            event=Event.model_validate_json(payload),
            token=worker_id,
        )

    def ack(self, lease: Lease) -> None:
        from django_harel.models import HarelQueue

        HarelQueue.objects.filter(seq=lease.seq).delete()

    def nack(self, lease: Lease, delay: float = 0.0) -> None:
        from django_harel.models import HarelQueue

        if delay > 0:
            # Park: mark as _PARKED so the group is unblocked but message is delayed
            HarelQueue.objects.filter(seq=lease.seq).update(
                locked_by=_PARKED,
                lock_expiry=time.time() + delay,
                available_at=time.time() + delay,
            )
        else:
            HarelQueue.objects.filter(seq=lease.seq).update(
                locked_by=None,
                lock_expiry=None,
            )

    def close(self) -> None:
        pass  # Django manages its own connection pool
