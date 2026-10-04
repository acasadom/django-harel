"""django_harel.signals: sent for committed steps only, once their transaction commits."""

import threading

import pytest
from django.db import transaction
from harel.engine.execution import Status
from harel.spec.states import Event

from django_harel import conf, signals
from django_harel.management.commands import harel_worker
from tests.app.models import Order, Stock
from tests.test_worker import CrashAfterStep

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def heard():
    """What each signal was sent, as (signal name, step, execution) in order."""
    received: list[tuple[str, object, object]] = []
    receivers = {}
    for name in ("step_committed", "execution_finished", "execution_failed"):

        def receiver(sender, step, execution, _name=name, **kwargs):
            received.append((_name, step, execution))

        receivers[name] = receiver
        getattr(signals, name).connect(receiver)
    yield received
    for name, receiver in receivers.items():
        getattr(signals, name).disconnect(receiver)


@pytest.fixture
def widgets():
    return Stock.objects.create(item="widget", available=5)


def _names(heard, execution_id=None):
    return [name for name, _, exe in heard if execution_id is None or exe.id == execution_id]


def test_steps_are_heard_once_their_transaction_commits(heard, widgets):
    runner = conf.durable_runner()
    with transaction.atomic():
        order = Order.objects.create(item="widget", qty=1)
        exe = runner.create("order", context={"order_id": order.pk})
        assert heard == [], "nothing is sent while the transaction is open"

    assert _names(heard) == ["step_committed"]
    step = heard[0][1]
    assert step.cause == "start" and step.to_path == "AwaitingPayment"
    assert step.actions == ("tests.app.actions.reserve_stock",)

    runner.process(exe.id, Event(kind="Pay"))
    assert _names(heard)[1:] == ["step_committed", "execution_finished"]
    _, step, execution = heard[-1]
    assert step.event_kind == "Pay" and step.from_status is Status.RUNNING and step.to_status is Status.DONE
    assert execution.outcome == "success"


def test_a_rolled_back_step_is_never_heard(heard, widgets):
    runner = conf.durable_runner()
    order = Order.objects.create(item="widget", qty=1)
    exe = runner.create("order", context={"order_id": order.pk})
    heard.clear()

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            runner.process(exe.id, Event(kind="Pay"))
            raise RuntimeError("the view fails after advancing the machine")

    assert heard == []


def test_a_failure_is_told_apart(heard, widgets):
    order = Order.objects.create(item="widget", qty=9)  # more than there is: the action raises
    exe = conf.durable_runner().create("order", context={"order_id": order.pk})

    assert exe.status is Status.FAILED
    assert _names(heard) == ["step_committed", "execution_failed"]
    assert heard[-1][1].to_status is Status.FAILED


def test_a_worker_step_that_rolled_back_is_never_heard(heard, widgets):
    runner = conf.distributed_runner()
    order = Order.objects.create(item="widget", qty=2)
    exe = runner.create("order", context={"order_id": order.pk})
    assert [s.cause for _, s, _ in heard] == ["create"]

    stop = threading.Event()
    worker = CrashAfterStep(conf.worker_runner(atomic_step=True).worker(worker_id="w"), stop)
    harel_worker.run(worker, stop, idle_sleep=0, atomic=True)

    assert worker.ran == 2, "the Start ran twice: the first attempt died before committing"
    starts = [s for _, s, e in heard if e.id == exe.id and s.cause == "event" and s.event_kind == "Start"]
    assert len(starts) == 1, "only the attempt that committed is heard"


def test_a_failing_receiver_changes_nothing(caplog, widgets):
    def broken(sender, **kwargs):
        raise RuntimeError("the receiver is broken")

    signals.step_committed.connect(broken)
    try:
        order = Order.objects.create(item="widget", qty=1)
        exe = conf.durable_runner().create("order", context={"order_id": order.pk})
    finally:
        signals.step_committed.disconnect(broken)

    assert exe.active_path == "AwaitingPayment"
    assert Order.objects.get(pk=order.pk).status == "reserved"
    assert "a receiver of a harel signal raised" in caplog.text


def test_nothing_is_prepared_without_receivers(monkeypatch, widgets):
    def no_copy(*args, **kwargs):
        raise AssertionError("the execution was copied for signals nobody listens to")

    assert not signals.listened()
    order = Order.objects.create(item="widget", qty=1)
    monkeypatch.setattr("harel.engine.execution.Execution.model_copy", no_copy)
    conf.durable_runner().create("order", context={"order_id": order.pk})
