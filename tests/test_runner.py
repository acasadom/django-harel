"""Tests for DjangoRunner — create, process, recover."""

from __future__ import annotations

import pytest
from harel.engine.execution import Status
from harel.spec.states import Event


@pytest.mark.django_db
def test_create_starts_execution(runner):
    exe = runner.create("job")
    # Pending → Running is an automatic transition; machine should be in Running after create
    assert exe.status == Status.RUNNING
    assert exe.active_path is not None
    assert "Running" in exe.active_path


@pytest.mark.django_db
def test_process_finish_event(runner):
    exe = runner.create("job")
    exe = runner.process(exe.id, Event(kind="Finish"))
    assert exe.status == Status.DONE
    assert exe.outcome == "success"


@pytest.mark.django_db
def test_process_fail_event(runner):
    exe = runner.create("job")
    exe = runner.process(exe.id, Event(kind="Fail"))
    assert exe.status == Status.DONE
    assert exe.outcome == "failure"


@pytest.mark.django_db
def test_process_unknown_execution_raises(runner):
    with pytest.raises(KeyError):
        runner.process("no-such-id", Event(kind="Finish"))


@pytest.mark.django_db
def test_execution_persisted_in_store(runner):
    exe = runner.create("job")
    loaded = runner.store.load(exe.id)
    assert loaded is not None
    assert loaded.id == exe.id
    assert loaded.status == exe.status
