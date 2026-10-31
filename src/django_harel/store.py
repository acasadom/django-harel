"""DjangoStore — harel's `ExecutionStore` on Django's database connection.

Every write runs in `transaction.atomic()` on the connection Django gives the current thread.
Inside a caller's `atomic()` block that is a savepoint: the machine's step commits, or rolls
back, together with the caller's own writes. Outside one it is a transaction of its own.

The tables are harel's (see `django_harel.schema`), so on SQLite and PostgreSQL harel's own
stores can read what this one writes, and the other way round.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from django.db import DEFAULT_DB_ALIAS, IntegrityError, connections, transaction
from harel.engine.execution import Execution, ExecutionPage, Status
from harel.engine.store.base import (
    DEFAULT_TRACE_MAX,
    DefinitionRecord,
    OutboxEntry,
    PendingWork,
    SpawnEntry,
    Step,
    StoreConflict,
    TimerOp,
    WorkerRecord,
    decode_offset,
    like_prefix,
    listing_page,
)
from harel.spec.states import Event

from django_harel.schema import check_vendor

# the summary fields `list_executions` reads out of the Execution JSON, in `listing_page` order
_SUMMARY_FIELDS = (
    "status",
    "outcome",
    "active_path",
    "parent_id",
    "finished_at",
    "definition_version",
    "updated_at",
)


def json_field(vendor: str, field: str) -> str:
    """An SQL expression reading `field` out of the `data` JSON, as text (NULL if null)."""
    if vendor == "sqlite":
        return f"json_extract(data, '$.{field}')"
    if vendor == "postgresql":
        return f"(data::jsonb ->> '{field}')"
    # MySQL/MariaDB: JSON_UNQUOTE turns a JSON null into the string 'null'
    value = f"JSON_EXTRACT(data, '$.{field}')"
    return f"(CASE WHEN JSON_TYPE({value}) = 'NULL' THEN NULL ELSE JSON_UNQUOTE({value}) END)"


def _number(value: Any, kind: type) -> Any:
    return kind(value) if value is not None else None


class DjangoStore:
    """An `ExecutionStore` over the Django database `using` (default: `"default"`).

    `savepoints` (default True): each write is a savepoint inside the caller's transaction, so a
    failed one leaves the caller's transaction usable. The worker turns them off when it runs
    each event in a transaction of its own, which any failure ends anyway — a round-trip less
    for every write. Without them a lost CAS also dooms that transaction: the step's actions
    wrote assuming a state that another writer has since moved on, so all of it rolls back —
    their writes and the claim — and the event runs again from the fresh state."""

    def __init__(
        self, using: str = DEFAULT_DB_ALIAS, *, trace_max: int = DEFAULT_TRACE_MAX, savepoints: bool = True
    ) -> None:
        self.using = using
        self.trace_max = trace_max
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

    def _execute(self, sql: str, params: Iterable = ()) -> Any:
        """Run one statement; return the cursor's rowcount."""
        with self._connection.cursor() as cur:
            cur.execute(sql, list(params))
            return cur.rowcount

    def _fetchall(self, sql: str, params: Iterable = ()) -> list[tuple]:
        with self._connection.cursor() as cur:
            cur.execute(sql, list(params))
            return cur.fetchall()

    def _fetchone(self, sql: str, params: Iterable = ()) -> Optional[tuple]:
        with self._connection.cursor() as cur:
            cur.execute(sql, list(params))
            return cur.fetchone()

    def _insert_seq(self, sql: str, params: Iterable) -> int:
        """Run an INSERT into a table keyed by an auto-increment `seq`; return the new seq."""
        with self._connection.cursor() as cur:
            if self._connection.features.can_return_columns_from_insert:
                cur.execute(f"{sql} RETURNING seq", list(params))
                return cur.fetchone()[0]
            cur.execute(sql, list(params))
            return cur.lastrowid

    def _insert_ignore(self, table: str, columns: tuple[str, ...], params: Iterable) -> None:
        """INSERT a row unless one with the same primary key is there."""
        cols, marks = ", ".join(columns), ", ".join(["%s"] * len(columns))
        vendor = self._vendor
        if vendor == "sqlite":
            sql = f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({marks})"
        elif vendor == "postgresql":
            sql = f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT DO NOTHING"
        else:
            sql = f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON DUPLICATE KEY UPDATE {columns[0]} = {columns[0]}"
        self._execute(sql, params)

    # --- the execution -------------------------------------------------------------------------
    def load(self, execution_id: str) -> Optional[Execution]:
        row = self._fetchone("SELECT data FROM harel_executions WHERE id = %s", [execution_id])
        return Execution.model_validate_json(row[0]) if row is not None else None

    def load_for_event(self, execution_id: str, event_id: str) -> tuple[Optional[Execution], bool]:
        """`load` and `is_processed` in one round-trip (the worker uses it when present)."""
        row = self._fetchone(
            "SELECT data, EXISTS (SELECT 1 FROM harel_processed_events "
            "WHERE execution_id = %s AND event_id = %s) FROM harel_executions WHERE id = %s",
            [execution_id, event_id, execution_id],
        )
        if row is None:
            return None, False
        return Execution.model_validate_json(row[0]), bool(row[1])

    def _write(self, exe: Execution) -> Optional[StoreConflict]:
        """The version-CAS write of `exe`, inside the caller's atomic block. A lost CAS is
        *returned*, having written nothing, for the caller to raise once out of its atomic
        block: raised inside one without a savepoint, it would doom the whole transaction —
        and harel goes on using it (it nacks the message)."""
        old = exe.version
        exe.version = old + 1
        data = exe.model_dump_json()
        updated = self._execute(
            "UPDATE harel_executions SET data = %s, version = %s WHERE id = %s AND version = %s",
            [data, exe.version, exe.id, old],
        )
        if updated:
            return None
        found = self._fetchone("SELECT version FROM harel_executions WHERE id = %s", [exe.id])
        if found is None and old == 0:
            try:
                # always a savepoint: a concurrent create fails this INSERT, and the caller's
                # transaction must survive it
                with transaction.atomic(using=self.using):
                    self._execute(
                        "INSERT INTO harel_executions (id, definition_id, data, version) VALUES (%s, %s, %s, %s)",
                        [exe.id, exe.definition_id, data, exe.version],
                    )
                return None
            except IntegrityError:
                found = None  # another writer created it first
        exe.version = old
        return StoreConflict(exe.id, expected=old, found=found[0] if found else None)

    def save(self, exe: Execution) -> None:
        old = exe.version
        try:
            with self._atomic():
                conflict = self._write(exe)
        except BaseException:
            exe.version = old
            raise
        if conflict is not None:
            self._doom()
            raise conflict

    def _doom(self) -> None:
        """Without savepoints, the caller's transaction is one step of the worker's: a lost CAS
        rolls all of it back (see the class)."""
        if not self.savepoints and self._connection.in_atomic_block:
            transaction.set_rollback(True, using=self.using)

    def commit(
        self,
        exe: Execution,
        emits: list[tuple[Optional[str], Event]],
        processed_event_id: Optional[str] = None,
        timers: tuple[TimerOp, ...] = (),
        spawns: tuple[tuple[str, str, dict], ...] = (),
        trace: Optional[dict] = None,
        step: Optional[Step] = None,
    ) -> list[int]:
        old = exe.version
        seqs: list[int] = []
        try:
            with self._atomic():
                conflict = self._write(exe)
                if conflict is None:
                    seqs = self._write_effects(exe, emits, processed_event_id, timers, spawns, trace)
                    if step is not None:
                        self._signal_on_commit(step, exe)
        except BaseException:
            exe.version = old
            raise
        if conflict is not None:
            self._doom()
            raise conflict
        return seqs

    def _signal_on_commit(self, step: Step, exe: Execution) -> None:
        """Send the signals for `step` once the transaction holding this commit commits —
        never for one that rolls back (see `django_harel.signals`)."""
        from django_harel import signals

        if signals.listened():
            committed = exe.model_copy(deep=True)
            transaction.on_commit(lambda: signals.send(step, committed), using=self.using)

    def _write_effects(
        self,
        exe: Execution,
        emits: list[tuple[Optional[str], Event]],
        processed_event_id: Optional[str],
        timers: tuple[TimerOp, ...],
        spawns: tuple[tuple[str, str, dict], ...],
        trace: Optional[dict],
    ) -> list[int]:
        """What a step writes besides the execution: its emits (returning their outbox seqs),
        its dedupe record, its children, its timers and its trace."""
        seqs = [
            self._insert_seq(
                "INSERT INTO harel_outbox (target_id, event) VALUES (%s, %s)",
                [target_id, event.model_dump_json()],
            )
            for target_id, event in emits
        ]
        if processed_event_id is not None:
            self._insert_ignore(
                "harel_processed_events", ("execution_id", "event_id"), [exe.id, processed_event_id]
            )
        for child_id, root_path, context in spawns:
            self._execute(
                "INSERT INTO harel_spawns (parent_id, child_id, root_path, context) VALUES (%s, %s, %s, %s)",
                [exe.id, child_id, root_path, json.dumps(context)],
            )
        for op in timers:
            self._execute("DELETE FROM harel_timers WHERE execution_id = %s AND path = %s", [exe.id, op.path])
            if op.action == "schedule":
                self._execute(
                    "INSERT INTO harel_timers (execution_id, path, fire_at) VALUES (%s, %s, %s)",
                    [exe.id, op.path, op.fire_at],
                )
        if trace is not None:
            self._write_trace(exe.id, trace)
        return seqs

    def is_processed(self, execution_id: str, event_id: str) -> bool:
        row = self._fetchone(
            "SELECT 1 FROM harel_processed_events WHERE execution_id = %s AND event_id = %s",
            [execution_id, event_id],
        )
        return row is not None

    def list_executions(
        self,
        *,
        status: Optional[Iterable[Status]] = None,
        definition_id: Optional[str] = None,
        roots_only: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> ExecutionPage:
        vendor = self._vendor
        field = {name: json_field(vendor, name) for name in _SUMMARY_FIELDS}
        where, params = ["1 = 1"], []
        if definition_id is not None:
            where.append("definition_id = %s")
            params.append(definition_id)
        if status is not None:
            statuses = [s.value for s in status]
            if not statuses:
                where.append("1 = 0")
            else:
                where.append(f"{field['status']} IN ({', '.join(['%s'] * len(statuses))})")
                params += statuses
        if roots_only:
            where.append(f"{field['parent_id']} IS NULL")
        off = decode_offset(cursor)
        rows = self._fetchall(
            f"SELECT id, definition_id, version, {', '.join(field.values())} FROM harel_executions "
            f"WHERE {' AND '.join(where)} ORDER BY id LIMIT %s OFFSET %s",
            [*params, limit + 1, off],
        )
        # the numbers come out as text from the JSON on some vendors
        rows = [(*r[:7], _number(r[7], float), _number(r[8], int), _number(r[9], float)) for r in rows]
        return listing_page(rows, limit, off)

    def ids_with_prefix(self, prefix: str) -> list[str]:
        # MySQL's LIKE escapes with a backslash by default, and reads '\\' as one backslash
        escape = "" if self._vendor == "mysql" else " ESCAPE '\\'"
        rows = self._fetchall(
            f"SELECT id FROM harel_executions WHERE id LIKE %s{escape}", [like_prefix(prefix)]
        )
        return [r[0] for r in rows]

    def purge(self, execution_id: str, expected_version: int) -> bool:
        with self._atomic():
            deleted = self._execute(
                "DELETE FROM harel_executions WHERE id = %s AND version = %s",
                [execution_id, expected_version],
            )
            if not deleted and self._fetchone("SELECT 1 FROM harel_executions WHERE id = %s", [execution_id]):
                return False  # it moved on: touch nothing
            for sql in (
                "DELETE FROM harel_processed_events WHERE execution_id = %s",
                "DELETE FROM harel_trace WHERE execution_id = %s",
                "DELETE FROM harel_timers WHERE execution_id = %s",
                "DELETE FROM harel_outbox WHERE target_id = %s",
                "DELETE FROM harel_spawns WHERE parent_id = %s",
            ):
                self._execute(sql, [execution_id])
            return bool(deleted)

    # --- the trace -----------------------------------------------------------------------------
    def _write_trace(self, execution_id: str, entry: dict) -> None:
        last = self._fetchone("SELECT MAX(idx) FROM harel_trace WHERE execution_id = %s", [execution_id])
        idx = 0 if last is None or last[0] is None else last[0] + 1
        self._execute(
            "INSERT INTO harel_trace (execution_id, idx, entry) VALUES (%s, %s, %s)",
            [execution_id, idx, json.dumps(entry)],
        )
        if self.trace_max:
            self._execute(
                "DELETE FROM harel_trace WHERE execution_id = %s AND idx <= %s",
                [execution_id, idx - self.trace_max],
            )

    def append_trace(self, execution_id: str, entry: dict) -> None:
        with self._atomic():
            self._write_trace(execution_id, entry)

    def read_trace(self, execution_id: str) -> list[dict]:
        rows = self._fetchall(
            "SELECT idx, entry FROM harel_trace WHERE execution_id = %s ORDER BY idx", [execution_id]
        )
        return [{**json.loads(entry), "index": idx} for idx, entry in rows]

    # --- deferred work -------------------------------------------------------------------------
    def pending_outbox(self) -> list[OutboxEntry]:
        rows = self._fetchall("SELECT seq, target_id, event FROM harel_outbox ORDER BY seq")
        return [
            OutboxEntry(seq, target_id, Event.model_validate_json(event)) for seq, target_id, event in rows
        ]

    def ack_outbox(self, seq: int) -> None:
        self._execute("DELETE FROM harel_outbox WHERE seq = %s", [seq])

    def pending_spawns(self) -> list[SpawnEntry]:
        rows = self._fetchall(
            "SELECT seq, parent_id, child_id, root_path, context FROM harel_spawns ORDER BY seq"
        )
        return [SpawnEntry(seq, pid, cid, rp, json.loads(ctx)) for seq, pid, cid, rp, ctx in rows]

    def ack_spawn(self, seq: int) -> None:
        self._execute("DELETE FROM harel_spawns WHERE seq = %s", [seq])

    def due_timers(self, now: float) -> list[tuple[str, str, float]]:
        rows = self._fetchall(
            "SELECT execution_id, path, fire_at FROM harel_timers WHERE fire_at <= %s ORDER BY fire_at", [now]
        )
        return [(eid, path, float(fire_at)) for eid, path, fire_at in rows]

    def delete_timer(self, execution_id: str, path: str, fire_at: float) -> None:
        self._execute(
            "DELETE FROM harel_timers WHERE execution_id = %s AND path = %s AND fire_at = %s",
            [execution_id, path, fire_at],
        )

    def pending_work(self, execution_id: str) -> PendingWork:
        """What is waiting for `execution_id`: its armed timers, the outbox entries addressed to
        it and the child spawns it issued — read by its id, for the admin and harel's monitor."""
        timers = self._fetchall(
            "SELECT path, fire_at FROM harel_timers WHERE execution_id = %s ORDER BY fire_at, path",
            [execution_id],
        )
        outbox = self._fetchall(
            "SELECT seq, target_id, event FROM harel_outbox WHERE target_id = %s ORDER BY seq", [execution_id]
        )
        spawns = self._fetchall(
            "SELECT seq, parent_id, child_id, root_path, context FROM harel_spawns "
            "WHERE parent_id = %s ORDER BY seq",
            [execution_id],
        )
        return PendingWork(
            timers=[(path, float(fire_at)) for path, fire_at in timers],
            outbox=[OutboxEntry(seq, tid, Event.model_validate_json(event)) for seq, tid, event in outbox],
            spawns=[SpawnEntry(seq, pid, cid, rp, json.loads(ctx)) for seq, pid, cid, rp, ctx in spawns],
        )

    # --- the workers' heartbeats ---------------------------------------------------------------
    def record_worker(self, record: WorkerRecord) -> None:
        """Store `record`, replacing the worker's previous heartbeat."""
        vendor = self._vendor
        if vendor == "mysql":
            sql = (
                "INSERT INTO harel_workers (worker_id, record) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE record = VALUES(record)"
            )
        else:
            sql = (
                "INSERT INTO harel_workers (worker_id, record) VALUES (%s, %s) "
                "ON CONFLICT (worker_id) DO UPDATE SET record = excluded.record"
            )
        with self._atomic():
            self._execute(sql, [record.worker_id, record.to_json()])

    def list_workers(self) -> list[WorkerRecord]:
        rows = self._fetchall("SELECT record FROM harel_workers ORDER BY worker_id")
        return [WorkerRecord.from_json(r[0]) for r in rows]

    def forget_worker(self, worker_id: str) -> None:
        with self._atomic():
            self._execute("DELETE FROM harel_workers WHERE worker_id = %s", [worker_id])

    # --- published machine versions ------------------------------------------------------------
    def publish_definition(self, record: DefinitionRecord) -> DefinitionRecord:
        """Store `record` unless a machine version with its `definition_id` and `version` is
        stored already — a version is stored once. Returns what is stored: `record`, or the one
        stored first (whose `digest` may differ)."""
        self._insert_ignore(
            "harel_definitions",
            ("definition_id", "version", "digest", "source", "published_at"),
            [
                record.definition_id,
                record.version,
                record.digest,
                json.dumps(record.source),
                record.published_at,
            ],
        )
        stored = self.load_definition(record.definition_id, record.version)
        assert stored is not None  # just stored, or stored before
        return stored

    def load_definition(self, definition_id: str, version: int) -> Optional[DefinitionRecord]:
        row = self._fetchone(
            "SELECT digest, source, published_at FROM harel_definitions "
            "WHERE definition_id = %s AND version = %s",
            [definition_id, version],
        )
        if row is None:
            return None
        digest, source, published_at = row
        return DefinitionRecord(definition_id, version, digest, json.loads(source), published_at)

    def close(self) -> None:
        """Nothing to release: Django owns the connection."""
