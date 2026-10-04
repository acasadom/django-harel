"""The two ways to run a machine from Django, against the test app's own tables.

- `DurableRunner(execution="inline")`: the request runs each event to the end, actions
  included, inside its transaction.
- `DistributedRunner(execution="inline")` + a `Worker`: the request writes the execution and
  its queued event in its transaction; a worker runs it later.
"""

import pytest
from django.db import transaction
from harel.engine.distributed import DistributedRunner
from harel.engine.durable import DurableRunner
from harel.engine.execution import Status
from harel.spec.states import Event

from django_harel import conf
from django_harel.store import DjangoStore
from django_harel.transport import DjangoTransport
from tests.app.actions import OutOfStock
from tests.app.models import Order, Stock

pytestmark = pytest.mark.django_db(transaction=True)


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def widgets():
    return Stock.objects.create(item="widget", available=5)


def _queued() -> int:
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM harel_transport_messages")
        return cur.fetchone()[0]


# --- in the request ------------------------------------------------------------------------------


def _durable(on_action_error: str = "raise") -> DurableRunner:
    return DurableRunner(
        DjangoStore(), conf.definitions(), execution="inline", on_action_error=on_action_error
    )


def test_inline_runs_the_actions_in_the_request(widgets):
    runner = _durable()
    with transaction.atomic():
        order = Order.objects.create(item="widget", qty=2)
        exe = runner.create("order", context={"order_id": order.pk}, execution_id=f"order-{order.pk}")

    assert exe.active_path.endswith("AwaitingPayment")
    assert Order.objects.get(pk=order.pk).status == "reserved"
    assert Stock.objects.get(pk="widget").available == 3

    exe = runner.process(exe.id, Event(kind="Pay"))
    assert exe.status is Status.DONE and exe.outcome == "success"
    assert Order.objects.get(pk=order.pk).status == "paid"


def test_a_raising_action_rolls_back_everything(widgets):
    runner = _durable(on_action_error="raise")
    with pytest.raises(OutOfStock):
        with transaction.atomic():
            order = Order.objects.create(item="widget", qty=9)
            runner.create("order", context={"order_id": order.pk}, execution_id="too-many")

    assert not Order.objects.exists()
    assert Stock.objects.get(pk="widget").available == 5
    assert runner.store.load("too-many") is None


def test_a_failing_action_dead_letters_with_fail(widgets):
    runner = _durable(on_action_error="fail")
    order = Order.objects.create(item="widget", qty=9)
    exe = runner.create("order", context={"order_id": order.pk})
    assert exe.status is Status.FAILED
    assert "OutOfStock" in exe.error


def test_the_callers_rollback_takes_the_machine_with_it(widgets):
    runner = _durable()
    order = Order.objects.create(item="widget", qty=1)
    exe = runner.create("order", context={"order_id": order.pk})

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            runner.process(exe.id, Event(kind="Pay"))
            raise RuntimeError("the view fails after advancing the machine")

    assert runner.store.load(exe.id).active_path.endswith("AwaitingPayment")
    assert Order.objects.get(pk=order.pk).status == "reserved"


# --- through the queue ---------------------------------------------------------------------------


def _distributed(clock=None) -> DistributedRunner:
    clock = clock or Clock()
    return DistributedRunner(
        DjangoStore(), DjangoTransport(clock=clock), conf.definitions(), clock=clock, execution="inline"
    )


def test_create_runs_nothing_in_the_request(widgets):
    runner = _distributed()
    order = Order.objects.create(item="widget", qty=2)
    exe = runner.create("order", context={"order_id": order.pk})

    assert exe.status is Status.PENDING
    assert Order.objects.get(pk=order.pk).status == "placed"
    assert _queued() == 1


def test_a_rolled_back_create_queues_nothing(widgets):
    runner = _distributed()
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            order = Order.objects.create(item="widget", qty=2)
            runner.create("order", context={"order_id": order.pk}, execution_id="never")
            raise RuntimeError("the view fails")

    assert runner.store.load("never") is None
    assert _queued() == 0


def test_the_worker_runs_what_the_request_queued(widgets):
    clock = Clock()
    runner = _distributed(clock)
    worker = runner.worker(worker_id="w1", clock=clock)
    order = Order.objects.create(item="widget", qty=2)
    exe = runner.create("order", context={"order_id": order.pk})

    assert worker.step()
    assert Order.objects.get(pk=order.pk).status == "reserved"
    assert not worker.step()

    with transaction.atomic():
        runner.send(exe.id, Event(kind="Pay"))
    assert worker.step()
    done = runner.store.load(exe.id)
    assert done.status is Status.DONE and done.outcome == "success"
    assert Order.objects.get(pk=order.pk).status == "paid"
    assert _queued() == 0


def test_the_worker_fires_the_timers(widgets):
    clock = Clock()
    runner = _distributed(clock)
    worker = runner.worker(worker_id="w1", clock=clock)
    order = Order.objects.create(item="widget", qty=1)
    exe = runner.create("order", context={"order_id": order.pk})
    worker.step()

    assert worker.fire_due_timers() == 0
    clock.now += 61
    assert worker.fire_due_timers() == 1
    assert worker.step()

    expired = runner.store.load(exe.id)
    assert expired.status is Status.DONE and expired.outcome == "expired"
    assert Order.objects.get(pk=order.pk).status == "expired"


# --- settings ------------------------------------------------------------------------------------


def test_the_runners_come_from_the_settings(settings):
    assert {"job", "order", "fulfilment"} <= set(conf.definitions())
    assert conf.durable_runner() is conf.durable_runner()
    assert conf.distributed_runner().execution == "inline"

    settings.DJANGO_HAREL = {"MACHINES": {"job": "tests/machines/job.stm"}}
    assert set(conf.definitions()) == {"job"}
