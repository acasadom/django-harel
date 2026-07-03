"""Test that DjangoStore.commit() participates in transaction.atomic().

This is the killer feature: advancing a statechart and saving domain models
happen in ONE transaction — either both commit or both roll back.
"""

from __future__ import annotations

import pytest
from django.db import models, transaction
from harel.spec.states import Event


# A minimal Django model used as the "domain model" in these tests
class Job(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "django_harel"


@pytest.fixture(autouse=True)
def _create_job_table(db):
    """Ensure the jobs table exists for the test run (using schema_editor)."""
    from django.db import connection

    with connection.schema_editor() as schema_editor:
        try:
            schema_editor.create_model(Job)
        except Exception:
            pass  # table may already exist from a previous test
    yield
    with connection.schema_editor() as schema_editor:
        try:
            schema_editor.delete_model(Job)
        except Exception:
            pass


@pytest.mark.django_db(transaction=True)
def test_statechart_advance_rolls_back_with_domain_model(runner):
    """If the outer transaction rolls back, the execution advance is undone too."""
    exe = runner.create("job")
    initial_version = exe.version

    try:
        with transaction.atomic():
            # Advance the statechart
            exe = runner.process(exe.id, Event(kind="Finish"))
            assert exe.version > initial_version  # committed inside the atomic block

            # Create a domain model in the same transaction
            Job.objects.create(name="order-123")

            raise RuntimeError("force rollback")
    except RuntimeError:
        pass

    # Both the execution advance AND the domain model must be rolled back
    reloaded = runner.store.load(exe.id)
    assert reloaded is not None
    assert reloaded.version == initial_version, "execution version must be rolled back"
    assert not Job.objects.filter(name="order-123").exists(), "domain model must be rolled back"


@pytest.mark.django_db(transaction=True)
def test_statechart_and_domain_model_commit_together(runner):
    """When no exception occurs, both the execution advance and domain model commit."""
    exe = runner.create("job")

    with transaction.atomic():
        exe = runner.process(exe.id, Event(kind="Finish"))
        Job.objects.create(name="order-456")

    # Both should be committed
    reloaded = runner.store.load(exe.id)
    assert reloaded is not None
    assert reloaded.outcome == "success"
    assert Job.objects.filter(name="order-456").exists()


@pytest.mark.django_db(transaction=True)
def test_nested_atomic_savepoint(runner):
    """An inner transaction.atomic() failure rolls back only the inner savepoint."""
    exe = runner.create("job")

    with transaction.atomic():
        # Outer: create a job
        Job.objects.create(name="outer-job")

        try:
            with transaction.atomic():  # inner → SAVEPOINT
                exe = runner.process(exe.id, Event(kind="Finish"))
                raise RuntimeError("inner rollback")
        except RuntimeError:
            pass

        # After inner rollback: execution is back to Running, but outer job persists
        reloaded = runner.store.load(exe.id)
        assert reloaded is not None

    # Outer committed: outer job exists
    assert Job.objects.filter(name="outer-job").exists()
    # Inner rolled back: execution still in Running (not Done)
    final = runner.store.load(exe.id)
    assert final is not None
    assert final.outcome != "success", "inner rollback must have undone the Finish event"
