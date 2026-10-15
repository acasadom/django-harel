"""ExecutionField: a model's machine as an attribute of the model."""

import threading

import pytest
from django.db import transaction
from harel.engine.execution import Status

from django_harel import conf, instance_for
from django_harel.management.commands import harel_worker
from tests.app.models import Ticket
from tests.test_worker import StopWhenIdle

pytestmark = pytest.mark.django_db(transaction=True)


def _drain():
    stop = threading.Event()
    harel_worker.run(
        StopWhenIdle(conf.worker_runner(atomic_step=True).worker(worker_id="w"), stop), stop, idle_sleep=0
    )


def test_autostart_queues_the_start_with_the_row():
    ticket = Ticket.objects.create(title="printer on fire")

    assert ticket.workflow.id == f"app.ticket.workflow:{ticket.pk}"
    assert ticket.workflow.status is Status.PENDING
    assert ticket.workflow.execution.context == {"ticket_id": ticket.pk}

    _drain()
    assert ticket.workflow.state == "Running"


def test_a_rolled_back_row_has_no_execution():
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            ticket = Ticket.objects.create(title="never saved")
            assert ticket.workflow.exists
            raise RuntimeError("the view fails")

    assert not ticket.workflow.exists


def test_send_queues_and_the_worker_runs_it():
    ticket = Ticket.objects.create(title="t")
    _drain()

    ticket.workflow.send("Fail", reason="no toner")
    assert ticket.workflow.state == "Running", "queued, not run"
    _drain()
    assert ticket.workflow.status is Status.DONE and ticket.workflow.outcome == "failure"


def test_a_request_run_field_starts_in_the_caller():
    ticket = Ticket.objects.create(title="review me")
    assert not ticket.review.exists, "only `workflow` starts with the row"

    exe = ticket.review.start()
    assert exe.active_path == "Running" and exe.context == {"title": "review me"}
    assert ticket.review.id != ticket.workflow.id

    exe = ticket.review.process("Finish")
    assert exe.outcome == "success"


def test_send_dedupes_on_the_event_id():
    ticket = Ticket.objects.create(title="t")
    _drain()
    ticket.workflow.send("Fail", event_id="webhook-1", reason="a")
    ticket.workflow.send("Fail", event_id="webhook-1", reason="a")
    _drain()
    assert ticket.workflow.outcome == "failure"


def test_instance_for_finds_the_row_back():
    ticket = Ticket.objects.create(title="t")
    assert instance_for(ticket.workflow.execution) == ticket

    ticket.review.start()
    assert instance_for(ticket.review.execution) == ticket

    other = conf.durable_runner().create("job", execution_id="not-a-field")
    assert instance_for(other) is None


def test_the_class_attribute_is_the_field():
    assert Ticket.workflow.definition_id == "job"
    with pytest.raises(ValueError, match="no primary key"):
        Ticket(title="unsaved").workflow.id


def test_fixtures_do_not_autostart():
    from django.db.models.signals import post_save

    ticket = Ticket(title="loaded")
    Ticket.objects.bulk_create([ticket])  # no post_save: nothing starts
    ticket = Ticket.objects.get(title="loaded")
    post_save.send(Ticket, instance=ticket, created=True, raw=True)  # what loaddata sends
    assert not ticket.workflow.exists
