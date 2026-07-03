"""DjangoStore — an ExecutionStore backed by Django's ORM.

The key property: `commit()` uses `transaction.atomic()`, which is nestable in
Django. If the caller already holds a `transaction.atomic()` context (e.g. in a
view or a signal), the statechart advance participates in that transaction via a
SAVEPOINT — rolling the outer transaction back also rolls back the execution
advance. This is the "dual-write-free" guarantee: domain models and statechart
state commit together or not at all.
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

from django.db import transaction as djtx
from harel.engine.execution import Execution, ExecutionPage, ExecutionSummary, Status
from harel.engine.store._base import (
    DEFAULT_TRACE_MAX,
    OutboxEntry,
    SpawnEntry,
    StoreConflict,
    TimerOp,
    _decode_offset,
    _encode_offset,
)
from harel.spec.states import Event


class DjangoStore:
    """ExecutionStore implementation over Django's ORM.

    Works with any Django-supported database (SQLite for tests, Postgres in
    production). Imports Django models lazily so the class can be imported
    before `django.setup()` is called (e.g. at module level in settings)."""

    def __init__(self, trace_max: int = DEFAULT_TRACE_MAX) -> None:
        self.trace_max = trace_max

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _models():
        from django_harel.models import (
            HarelExecution,
            HarelOutbox,
            HarelProcessedEvent,
            HarelSpawn,
            HarelTimer,
            HarelTrace,
        )

        return HarelExecution, HarelOutbox, HarelProcessedEvent, HarelSpawn, HarelTimer, HarelTrace

    # ------------------------------------------------------------------
    # ExecutionStore protocol
    # ------------------------------------------------------------------

    def load(self, execution_id: str) -> Optional[Execution]:
        HarelExecution, *_ = self._models()
        try:
            obj = HarelExecution.objects.get(pk=execution_id)
        except HarelExecution.DoesNotExist:
            return None
        return Execution.model_validate_json(obj.data)

    def list_executions(
        self,
        *,
        status: Optional[Iterable[Status]] = None,
        definition_id: Optional[str] = None,
        roots_only: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> ExecutionPage:
        HarelExecution, *_ = self._models()
        qs = HarelExecution.objects.order_by("id")
        if definition_id is not None:
            qs = qs.filter(definition_id=definition_id)
        if status is not None:
            qs = qs.filter(status__in=[s.value for s in status])
        if roots_only:
            qs = qs.filter(parent_id__isnull=True)
        off = _decode_offset(cursor)
        rows = qs[off : off + limit + 1]
        items = [
            ExecutionSummary(
                id=r.id,
                definition_id=r.definition_id,
                version=r.version,
                status=r.status,
                outcome=None,  # not stored as a separate column; omitted for list view
                active_path=r.active_path or None,
                parent_id=r.parent_id,
            )
            for r in rows[:limit]
        ]
        nxt = _encode_offset(off + limit) if len(rows) > limit else None
        return ExecutionPage(items=items, next_cursor=nxt)

    def save(self, exe: Execution) -> None:
        self.commit(exe, [])

    def commit(
        self,
        exe: Execution,
        emits: list[tuple[Optional[str], Event]],
        processed_event_id: Optional[str] = None,
        timers: tuple[TimerOp, ...] = (),
        spawns: tuple[tuple[str, str, dict], ...] = (),
        trace: Optional[dict] = None,
    ) -> None:
        """Atomically advance the Execution (CAS on version) and write all
        deferred work (outbox, spawns, timers, trace) in a single transaction.

        If the caller is already inside a `transaction.atomic()` block, this
        creates a SAVEPOINT and participates in the outer transaction — so the
        domain models and statechart state commit (or roll back) together."""
        HarelExecution, HarelOutbox, HarelProcessedEvent, HarelSpawn, HarelTimer, HarelTrace = self._models()
        old = exe.version
        exe.version = old + 1
        data = exe.model_dump_json()
        status_val = exe.status.value
        active_path_val = exe.active_path or ""
        parent_id_val = exe.parent_id

        try:
            with djtx.atomic():
                # CAS: UPDATE WHERE version = old
                updated = HarelExecution.objects.filter(pk=exe.id, version=old).update(
                    data=data,
                    version=exe.version,
                    status=status_val,
                    active_path=active_path_val,
                    parent_id=parent_id_val,
                )
                if updated == 0:
                    # Either a brand-new execution (version=0 and no row) or a conflict
                    if old == 0 and not HarelExecution.objects.filter(pk=exe.id).exists():
                        HarelExecution.objects.create(
                            id=exe.id,
                            definition_id=exe.definition_id,
                            data=data,
                            version=1,
                            status=status_val,
                            active_path=active_path_val,
                            parent_id=parent_id_val,
                        )
                    else:
                        exe.version = old
                        raise StoreConflict(exe.id, expected=old, found=None)

                # Outbox: events to deliver after this transaction commits
                for target_id, event in emits:
                    HarelOutbox.objects.create(
                        target_id=target_id,
                        event=event.model_dump_json(),
                    )

                # Deduplication: mark this event as processed
                if processed_event_id is not None:
                    HarelProcessedEvent.objects.get_or_create(
                        execution_id=exe.id,
                        event_id=processed_event_id,
                    )

                # Orthogonal spawns: child-execution creation intents
                for child_id, root_path, context in spawns:
                    HarelSpawn.objects.create(
                        parent_id=exe.id,
                        child_id=child_id,
                        root_path=root_path,
                        context=json.dumps(context),
                    )

                # Timers: schedule or cancel
                for op in timers:
                    if op.action == "schedule":
                        HarelTimer.objects.update_or_create(
                            execution_id=exe.id,
                            path=op.path,
                            defaults={"fire_at": op.fire_at},
                        )
                    else:
                        HarelTimer.objects.filter(
                            execution_id=exe.id,
                            path=op.path,
                        ).delete()

                # Trace: append + ring-cap
                if trace is not None:
                    self._write_trace(exe.id, trace, HarelTrace)

        except StoreConflict:
            raise
        except Exception:
            exe.version = old
            raise

    def _write_trace(self, execution_id: str, entry: dict, HarelTrace) -> None:
        from django.db.models import Max

        agg = HarelTrace.objects.filter(execution_id=execution_id).aggregate(max_idx=Max("idx"))
        next_idx = (agg["max_idx"] if agg["max_idx"] is not None else -1) + 1
        HarelTrace.objects.create(execution_id=execution_id, idx=next_idx, entry=json.dumps(entry))
        if self.trace_max:
            # Delete rows older than the ring: keep the last trace_max entries
            cutoff = next_idx - self.trace_max
            if cutoff >= 0:
                HarelTrace.objects.filter(execution_id=execution_id, idx__lte=cutoff).delete()

    def append_trace(self, execution_id: str, entry: dict) -> None:
        _, _, _, _, _, HarelTrace = self._models()
        with djtx.atomic():
            self._write_trace(execution_id, entry, HarelTrace)

    def read_trace(self, execution_id: str) -> list[dict]:
        _, _, _, _, _, HarelTrace = self._models()
        rows = HarelTrace.objects.filter(execution_id=execution_id).order_by("idx")
        return [{**json.loads(r.entry), "index": r.idx} for r in rows]

    def is_processed(self, execution_id: str, event_id: str) -> bool:
        _, _, HarelProcessedEvent, *_ = self._models()
        return HarelProcessedEvent.objects.filter(
            execution_id=execution_id,
            event_id=event_id,
        ).exists()

    def pending_outbox(self) -> list[OutboxEntry]:
        _, HarelOutbox, *_ = self._models()
        rows = HarelOutbox.objects.order_by("seq")
        return [
            OutboxEntry(
                seq=r.seq,
                target_id=r.target_id,
                event=Event.model_validate_json(r.event),
            )
            for r in rows
        ]

    def ack_outbox(self, seq: int) -> None:
        _, HarelOutbox, *_ = self._models()
        HarelOutbox.objects.filter(seq=seq).delete()

    def pending_spawns(self) -> list[SpawnEntry]:
        _, _, _, HarelSpawn, *_ = self._models()
        rows = HarelSpawn.objects.order_by("seq")
        return [
            SpawnEntry(
                seq=r.seq,
                parent_id=r.parent_id,
                child_id=r.child_id,
                root_path=r.root_path,
                context=json.loads(r.context),
            )
            for r in rows
        ]

    def ack_spawn(self, seq: int) -> None:
        _, _, _, HarelSpawn, *_ = self._models()
        HarelSpawn.objects.filter(seq=seq).delete()

    def due_timers(self, now: float) -> list[tuple[str, str, float]]:
        _, _, _, _, HarelTimer, _ = self._models()
        rows = HarelTimer.objects.filter(fire_at__lte=now).order_by("fire_at")
        return [(r.execution_id, r.path, float(r.fire_at)) for r in rows]

    def delete_timer(self, execution_id: str, path: str, fire_at: float) -> None:
        _, _, _, _, HarelTimer, _ = self._models()
        HarelTimer.objects.filter(
            execution_id=execution_id,
            path=path,
            fire_at=fire_at,
        ).delete()

    def close(self) -> None:
        pass  # Django manages its own connection pool
