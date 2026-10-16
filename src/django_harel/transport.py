"""DjangoTransport — harel's `Transport` on Django's database connection.

The queue lives in harel's transport tables (see `django_harel.schema`), next to the store's.
A `publish` from a request runs on the request's connection, so it is part of the request's
transaction: if the request rolls back, the event was never sent. That is why the transport
declares `shares_store_transaction` — harel then lets a failed publish reach the caller.

Two layouts, as in harel:

- **SQLite**: the lease is on the message row, and claims serialize on SQLite's write lock.
- **PostgreSQL, MySQL**: the lease is on the group row, and claims take different groups in
  parallel with `SELECT … FOR UPDATE SKIP LOCKED`. `publish` and `ack` both lock the group
  row, so an `ack` that drains a group can't delete it under a `publish` still in flight in
  another transaction (which would leave that message with no group to be claimed by).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Optional

from django.db import DEFAULT_DB_ALIAS, OperationalError, connections, transaction
from harel.engine.transport.base import PARKED, Lease
from harel.spec.states import Event

from django_harel.schema import QUEUE_LOG, check_vendor

MESSAGES = "harel_transport_messages"
GROUPS = "harel_transport_groups"

# MySQL: how many claimable groups a claim reads before trying to lock one
_CANDIDATES = 16

# SQLite: the group that waited longest with nothing in flight, and its head — harel's own claim
# (`SqliteTransport`), so a native harel worker and this transport share the queue. `{routing}`
# is `_routing_filter`'s — empty when a claim isn't restricted to some machines.
_SQLITE_CLAIM = (
    f"SELECT m.seq, m.group_id, m.event FROM {GROUPS} g "
    f"JOIN {MESSAGES} m ON m.seq = (SELECT MIN(seq) FROM {MESSAGES} WHERE group_id = g.group_id) "
    "WHERE g.priority >= %s{routing} AND NOT EXISTS ("
    f"  SELECT 1 FROM {MESSAGES} x WHERE x.group_id = g.group_id "
    f"  AND x.locked_by IS NOT NULL AND x.lock_expiry >= %s"
    f") ORDER BY g.last_claimed_at, g.rowid LIMIT 1"
)


def _routing_filter(routing_keys: Optional[frozenset[str]], column: str) -> tuple[str, list]:
    """The SQL condition (`AND ...`) and its params restricting a claim to `routing_keys`'
    groups — none when `routing_keys` is None. Mirrors harel's own `Transport`s: an empty
    `routing_keys` is handled by the caller (`claim` returns `None` without a query)."""
    if not routing_keys:
        return "", []
    keys = sorted(routing_keys)
    return f" AND {column} IN ({', '.join(['%s'] * len(keys))})", keys


class DjangoTransport:
    """A `Transport` over the Django database `using` (default: `"default"`). `clock` gives
    the lease deadlines (seconds since the epoch). `savepoints` as for `DjangoStore`."""

    # its writes go into the store's transaction when both use the same database
    shares_store_transaction = True

    def __init__(
        self,
        using: str = DEFAULT_DB_ALIAS,
        clock: Callable[[], float] = time.time,
        *,
        savepoints: bool = True,
    ) -> None:
        self.using = using
        self._clock = clock
        self.savepoints = savepoints

    # --- helpers -----------------------------------------------------------------------------
    def _atomic(self) -> Any:
        return transaction.atomic(using=self.using, savepoint=self.savepoints)

    @property
    def _connection(self) -> Any:
        return connections[self.using]

    @property
    def _vendor(self) -> str:
        vendor = self._connection.vendor
        check_vendor(vendor)
        return vendor

    def _execute(self, sql: str, params: list) -> int:
        with self._connection.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def _fetchone(self, sql: str, params: list) -> Optional[tuple]:
        with self._connection.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _insert_message(self, group_id: str, payload: str) -> int:
        """Insert a queued message; return its `seq`."""
        sql = f"INSERT INTO {MESSAGES} (group_id, event) VALUES (%s, %s)"
        with self._connection.cursor() as cur:
            if self._connection.features.can_return_columns_from_insert:
                cur.execute(f"{sql} RETURNING seq", [group_id, payload])
                return cur.fetchone()[0]
            cur.execute(sql, [group_id, payload])
            return cur.lastrowid

    def _log_published(self, seq: int) -> None:
        """When `seq` was published, for the dashboard's "oldest queued" (see
        `django_harel.schema.QUEUE_LOG`) — outside harel's own schema, so a message published
        before this table existed has no row, and its age shows as unknown rather than wrong."""
        self._execute(f"INSERT INTO {QUEUE_LOG} (seq, published_at) VALUES (%s, %s)", [seq, self._clock()])

    # --- the protocol --------------------------------------------------------------------------
    def publish(
        self, group_id: str, event: Event, priority: int = 0, routing_key: Optional[str] = None
    ) -> None:
        """Enqueue `event` in `group_id`. `priority` and `routing_key` count on the group's
        first publish only; a group is deleted once drained, so every publish passes the
        execution's own (harel's engine does this for us)."""
        payload = event.model_dump_json()
        vendor = self._vendor
        with self._atomic():
            if vendor == "sqlite":
                seq = self._insert_message(group_id, payload)
                self._execute(
                    f"INSERT OR IGNORE INTO {GROUPS} (group_id, priority, routing_key) VALUES (%s, %s, %s)",
                    [group_id, priority, routing_key],
                )
                self._log_published(seq)
                return
            # create the group, or lock it if it's there (keeping its lease, priority, routing_key)
            if vendor == "postgresql":
                upsert = (
                    f"INSERT INTO {GROUPS} (group_id, locked_by, lock_expiry, priority, routing_key) "
                    f"VALUES (%s, NULL, NULL, %s, %s) ON CONFLICT (group_id) DO UPDATE SET priority = {GROUPS}.priority"
                )
            else:
                upsert = (
                    f"INSERT INTO {GROUPS} (group_id, locked_by, lock_expiry, priority, routing_key) "
                    "VALUES (%s, NULL, NULL, %s, %s) ON DUPLICATE KEY UPDATE priority = priority"
                )
            self._execute(upsert, [group_id, priority, routing_key])
            seq = self._insert_message(group_id, payload)
            self._log_published(seq)

    def claim(
        self,
        worker_id: str,
        visibility: float,
        min_priority: int = 0,
        routing_keys: Optional[frozenset[str]] = None,
    ) -> Optional[Lease]:
        """Lease the head of the group that waited longest with nothing in flight, among
        `routing_keys`' groups when given. An empty `routing_keys` claims nothing — harel's own
        contract (`assert_machine_routing_contract`)."""
        if routing_keys is not None and not routing_keys:
            return None
        if self._vendor == "sqlite":
            return self._claim_sqlite(worker_id, visibility, min_priority, routing_keys)
        return self._claim_group_lock(worker_id, visibility, min_priority, routing_keys)

    def _doomed(self) -> bool:
        """The caller's transaction is set to roll back (a worker step whose commit lost its
        CAS): the rollback ends the lease it took, so there is nothing to ack or nack — and a
        doomed transaction can't run another query."""
        return self._connection.in_atomic_block and transaction.get_rollback(using=self.using)

    def ack(self, lease: Lease) -> None:
        """The message was handled: remove it and free its group."""
        if self._doomed():
            return
        if self._vendor == "sqlite":
            with self._atomic():
                self._execute(f"DELETE FROM {MESSAGES} WHERE seq = %s", [lease.seq])
                self._execute(f"DELETE FROM {QUEUE_LOG} WHERE seq = %s", [lease.seq])
                self._execute(
                    f"DELETE FROM {GROUPS} WHERE group_id = %s AND NOT EXISTS "
                    f"(SELECT 1 FROM {MESSAGES} WHERE group_id = %s)",
                    [lease.group_id, lease.group_id],
                )
            return
        with self._atomic():
            holder = self._fetchone(
                f"SELECT locked_by FROM {GROUPS} WHERE group_id = %s FOR UPDATE", [lease.group_id]
            )
            if holder is None or holder[0] != lease.token:
                return  # the lease expired and another worker holds the group now
            self._execute(f"DELETE FROM {MESSAGES} WHERE seq = %s", [lease.seq])
            self._execute(f"DELETE FROM {QUEUE_LOG} WHERE seq = %s", [lease.seq])
            # a locking read: it sees what publishes committed while we waited for the group
            more = self._fetchone(
                f"SELECT seq FROM {MESSAGES} WHERE group_id = %s ORDER BY seq LIMIT 1 FOR UPDATE",
                [lease.group_id],
            )
            if more is not None:
                # back of the round-robin: served groups sort after never-served ones
                self._execute(
                    f"UPDATE {GROUPS} SET locked_by = NULL, lock_expiry = %s WHERE group_id = %s",
                    [self._clock(), lease.group_id],
                )
            else:
                # drained: dropped, so its priority is set again by the next publish
                self._execute(f"DELETE FROM {GROUPS} WHERE group_id = %s", [lease.group_id])

    def nack(self, lease: Lease, delay: float = 0.0) -> None:
        """Return the message: claimable again at once, or parked for `delay` seconds."""
        if self._doomed():
            return
        if self._vendor == "sqlite":
            if delay > 0:
                self._execute(
                    f"UPDATE {MESSAGES} SET locked_by = %s, lock_expiry = %s WHERE seq = %s",
                    [PARKED, self._clock() + delay, lease.seq],
                )
            else:
                self._execute(
                    f"UPDATE {MESSAGES} SET locked_by = NULL, lock_expiry = NULL WHERE seq = %s", [lease.seq]
                )
            return
        if delay > 0:
            # parked: the group keeps the lease token until `delay` passes
            self._execute(
                f"UPDATE {GROUPS} SET lock_expiry = %s WHERE group_id = %s AND locked_by = %s",
                [self._clock() + delay, lease.group_id, lease.token],
            )
        else:
            self._execute(
                f"UPDATE {GROUPS} SET locked_by = NULL, lock_expiry = NULL WHERE group_id = %s AND locked_by = %s",
                [lease.group_id, lease.token],
            )

    def close(self) -> None:
        """Nothing to release: Django owns the connection."""

    # --- claims --------------------------------------------------------------------------------
    def _claim_sqlite(
        self, worker_id: str, visibility: float, min_priority: int, routing_keys: Optional[frozenset[str]]
    ) -> Optional[Lease]:
        now = self._clock()
        routing, routing_params = _routing_filter(routing_keys, "g.routing_key")
        sql = _SQLITE_CLAIM.format(routing=routing)
        try:
            # always a savepoint: the lock error below is caught, and the transaction goes on
            with transaction.atomic(using=self.using):
                row = self._fetchone(sql, [min_priority, *routing_params, now])
                if row is None:
                    return None
                seq, group_id, payload = row
                self._execute(
                    f"UPDATE {GROUPS} SET last_claimed_at = %s WHERE group_id = %s", [now, group_id]
                )
                self._execute(
                    f"UPDATE {MESSAGES} SET locked_by = %s, lock_expiry = %s WHERE seq = %s",
                    [worker_id, now + visibility, seq],
                )
        except OperationalError as exc:
            # another process took SQLite's write lock between our read and our write: our read
            # is stale, so there's nothing safe to lease now — the worker claims again
            if "locked" in str(exc):
                return None
            raise
        return Lease(seq, group_id, Event.model_validate_json(payload), token=worker_id)

    def _claim_group_lock(
        self, worker_id: str, visibility: float, min_priority: int, routing_keys: Optional[frozenset[str]]
    ) -> Optional[Lease]:
        now = self._clock()
        token = f"{worker_id}:{uuid.uuid4().hex}"
        with self._atomic():
            while True:
                group_id = self._lock_claimable_group(now, min_priority, routing_keys)
                if group_id is None:
                    return None
                self._execute(
                    f"UPDATE {GROUPS} SET locked_by = %s, lock_expiry = %s WHERE group_id = %s",
                    [token, now + visibility, group_id],
                )
                head = self._fetchone(
                    f"SELECT seq, event FROM {MESSAGES} WHERE group_id = %s ORDER BY seq LIMIT 1", [group_id]
                )
                if head is not None:
                    return Lease(head[0], group_id, Event.model_validate_json(head[1]), token=token)
                # a group with no messages left (its drain was interrupted): drop it, try the next
                self._execute(f"DELETE FROM {GROUPS} WHERE group_id = %s", [group_id])

    def _lock_claimable_group(
        self, now: float, min_priority: int, routing_keys: Optional[frozenset[str]]
    ) -> Optional[str]:
        """Lock the claimable group served longest ago (never served first), skipping groups
        another transaction holds, or whose `routing_key` isn't one of `routing_keys` when
        given; None if there is none."""
        routing, routing_params = _routing_filter(routing_keys, "routing_key")
        claimable = f"(locked_by IS NULL OR lock_expiry < %s) AND priority >= %s{routing}"
        if self._vendor == "postgresql":
            row = self._fetchone(
                f"SELECT group_id FROM {GROUPS} WHERE {claimable} "
                "ORDER BY COALESCE(lock_expiry, 0), group_id LIMIT 1 FOR UPDATE SKIP LOCKED",
                [now, min_priority, *routing_params],
            )
            return row[0] if row is not None else None
        # InnoDB locks every row a locking read examines — sorting the candidates would lock
        # them all. So: pick candidates without locking, then lock one by its primary key.
        while True:
            with self._connection.cursor() as cur:
                cur.execute(
                    f"SELECT group_id FROM {GROUPS} WHERE {claimable} "
                    f"ORDER BY COALESCE(lock_expiry, 0), group_id LIMIT {_CANDIDATES}",
                    [now, min_priority, *routing_params],
                )
                candidates = [r[0] for r in cur.fetchall()]
            if not candidates:
                return None
            for group_id in candidates:
                row = self._fetchone(
                    f"SELECT group_id FROM {GROUPS} WHERE group_id = %s AND {claimable} FOR UPDATE SKIP LOCKED",
                    [group_id, now, min_priority, *routing_params],
                )
                if row is not None:
                    return row[0]
            if len(candidates) < _CANDIDATES:
                return None  # every claimable group is held by another transaction
