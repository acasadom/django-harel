"""Contract tests for DjangoStore — mirrors the harel store contract suite."""

from __future__ import annotations

import pytest
from harel.engine.execution import Execution, Status
from harel.engine.store._base import StoreConflict, TimerOp
from harel.spec.states import Event


@pytest.mark.django_db
def test_load_missing_returns_none(store):
    assert store.load("does-not-exist") is None


@pytest.mark.django_db
def test_commit_first_write(store):
    exe = Execution(definition_id="job")
    assert exe.version == 0
    store.commit(exe, [])
    assert exe.version == 1
    loaded = store.load(exe.id)
    assert loaded is not None
    assert loaded.version == 1
    assert loaded.definition_id == "job"


@pytest.mark.django_db
def test_save_delegates_to_commit(store):
    exe = Execution(definition_id="job")
    store.save(exe)
    assert exe.version == 1
    assert store.load(exe.id) is not None


@pytest.mark.django_db
def test_commit_cas_conflict(store):
    exe = Execution(definition_id="job")
    store.commit(exe, [])  # version -> 1

    # Simulate a stale copy (version still at 0)
    stale = Execution(id=exe.id, definition_id="job")  # version=0
    with pytest.raises(StoreConflict) as exc_info:
        store.commit(stale, [])
    assert exc_info.value.execution_id == exe.id
    assert exc_info.value.expected == 0


@pytest.mark.django_db
def test_commit_outbox_and_ack(store):
    exe = Execution(definition_id="job")
    event = Event(kind="Done")
    store.commit(exe, [(None, event)])

    entries = store.pending_outbox()
    assert len(entries) == 1
    assert entries[0].event.kind == "Done"
    assert entries[0].target_id is None

    store.ack_outbox(entries[0].seq)
    assert store.pending_outbox() == []


@pytest.mark.django_db
def test_commit_outbox_with_target(store):
    exe = Execution(definition_id="job")
    event = Event(kind="Finished")
    store.commit(exe, [("parent-id-123", event)])

    entries = store.pending_outbox()
    assert entries[0].target_id == "parent-id-123"


@pytest.mark.django_db
def test_is_processed_dedup(store):
    exe = Execution(definition_id="job")
    event_id = "evt-abc-123"
    assert not store.is_processed(exe.id, event_id)
    store.commit(exe, [], processed_event_id=event_id)
    assert store.is_processed(exe.id, event_id)


@pytest.mark.django_db
def test_spawn_outbox_and_ack(store):
    exe = Execution(definition_id="job")
    store.commit(exe, [], spawns=(("child-1", "branch/Region", {"x": 1}),))

    spawns = store.pending_spawns()
    assert len(spawns) == 1
    assert spawns[0].child_id == "child-1"
    assert spawns[0].root_path == "branch/Region"
    assert spawns[0].context == {"x": 1}

    store.ack_spawn(spawns[0].seq)
    assert store.pending_spawns() == []


@pytest.mark.django_db
def test_timers_schedule_and_due(store):
    exe = Execution(definition_id="job")
    op = TimerOp(action="schedule", path="job/Running", fire_at=1000.0)
    store.commit(exe, [], timers=(op,))

    assert store.due_timers(999.0) == []
    due = store.due_timers(1001.0)
    assert len(due) == 1
    assert due[0] == (exe.id, "job/Running", 1000.0)


@pytest.mark.django_db
def test_timer_cancel(store):
    exe = Execution(definition_id="job")
    store.commit(exe, [], timers=(TimerOp(action="schedule", path="job/Running", fire_at=1000.0),))
    exe2 = store.load(exe.id)
    store.commit(exe2, [], timers=(TimerOp(action="cancel", path="job/Running"),))
    assert store.due_timers(9999.0) == []


@pytest.mark.django_db
def test_delete_timer_stale_fire_at_noop(store):
    exe = Execution(definition_id="job")
    store.commit(exe, [], timers=(TimerOp(action="schedule", path="job/Running", fire_at=1000.0),))
    # Delete with a different fire_at — should be a no-op (timer still exists)
    store.delete_timer(exe.id, "job/Running", 999.0)
    assert len(store.due_timers(9999.0)) == 1


@pytest.mark.django_db
def test_trace_ring_buffer(store):
    exe = Execution(definition_id="job")
    store.trace_max = 3
    for i in range(5):
        step = store.load(exe.id)
        if step is None:
            step = exe
        store.commit(step, [], trace={"step": i})
        exe = store.load(exe.id)

    trace = store.read_trace(exe.id)
    assert len(trace) == 3
    assert trace[-1]["step"] == 4


@pytest.mark.django_db
def test_list_executions_filter_by_status(store):
    exe1 = Execution(definition_id="job")
    store.commit(exe1, [])
    exe2 = Execution(definition_id="job")
    store.commit(exe2, [])

    page = store.list_executions()
    assert len(page.items) == 2

    page_running = store.list_executions(status=[Status.PENDING])
    assert len(page_running.items) == 2  # both are PENDING by default
