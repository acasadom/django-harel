"""manage.py harel_worker."""

import threading

import pytest
from django.core.management import call_command
from harel.spec.states import Event

from django_harel import conf
from django_harel.management.commands import harel_worker
from tests.app.models import Order, Stock

pytestmark = pytest.mark.django_db(transaction=True)


class StopWhenIdle:
    """A worker that stops its loop the first time it finds nothing to do."""

    def __init__(self, worker, stop: threading.Event) -> None:
        self._worker, self._stop = worker, stop

    def step(self) -> bool:
        return self._worker.step()

    def fire_due_timers(self) -> int:
        fired = self._worker.fire_due_timers()
        if not fired:
            self._stop.set()
        return fired


def test_the_loop_runs_the_queue_until_stopped():
    Stock.objects.create(item="widget", available=5)
    runner = conf.distributed_runner()
    order = Order.objects.create(item="widget", qty=2)
    exe = runner.create("order", context={"order_id": order.pk})
    runner.send(exe.id, Event(kind="Pay"))

    stop = threading.Event()
    harel_worker.run(StopWhenIdle(runner.worker(worker_id="w"), stop), stop, idle_sleep=0)

    assert Order.objects.get(pk=order.pk).status == "paid"
    assert runner.store.load(exe.id).outcome == "success"


class CrashAfterStep(StopWhenIdle):
    """A worker that dies once right after running its first event — its actions have
    written, its transaction hasn't committed. Counts the events it ran."""

    def __init__(self, worker, stop: threading.Event) -> None:
        super().__init__(worker, stop)
        self.ran = 0

    def step(self) -> bool:
        progressed = self._worker.step()
        if progressed:
            self.ran += 1
            if self.ran == 1:
                raise RuntimeError("the worker dies before the event's transaction commits")
        return progressed


def test_an_atomic_step_leaves_nothing_behind_when_it_dies():
    Stock.objects.create(item="widget", available=5)
    runner = conf.distributed_runner()
    order = Order.objects.create(item="widget", qty=2)
    exe = runner.create("order", context={"order_id": order.pk})
    stop = threading.Event()
    worker = CrashAfterStep(runner.worker(worker_id="w", visibility=60), stop)

    harel_worker.run(worker, stop, idle_sleep=0, atomic=True)

    # the Start ran twice — the crashed run rolled back, so it was claimable again at once —
    # and its writes landed once
    assert worker.ran == 2
    assert Stock.objects.get(pk="widget").available == 3
    assert Order.objects.get(pk=order.pk).status == "reserved"
    assert runner.store.load(exe.id).active_path.endswith("AwaitingPayment")


def test_a_non_atomic_step_keeps_what_its_actions_wrote():
    Stock.objects.create(item="widget", available=5)
    runner = conf.distributed_runner()
    order = Order.objects.create(item="widget", qty=2)
    runner.create("order", context={"order_id": order.pk})
    stop = threading.Event()

    worker = CrashAfterStep(runner.worker(worker_id="w", visibility=60), stop)

    harel_worker.run(worker, stop, idle_sleep=0, atomic=False)

    # the actions' writes stayed, and the event is leased until its lease ends: it ran once
    assert worker.ran == 1
    assert Order.objects.get(pk=order.pk).status == "reserved"
    assert Stock.objects.get(pk="widget").available == 3


def test_the_loop_reconnects_when_the_database_drops_it(caplog):
    from django.db import connection

    if connection.vendor != "postgresql":
        pytest.skip("drops the worker's session with pg_terminate_backend")
    import psycopg

    Stock.objects.create(item="widget", available=5)
    runner = conf.distributed_runner()
    order = Order.objects.create(item="widget", qty=1)
    runner.create("order", context={"order_id": order.pk})

    # the database ends the worker's session under it (a restart, a failover, an idle timeout)
    pid = connection.cursor().connection.info.backend_pid
    params = connection.get_connection_params()
    with psycopg.connect(
        host=params["host"], port=params["port"], user=params["user"],
        password=params["password"], dbname=params["dbname"], autocommit=True,
    ) as admin:  # fmt: skip
        admin.execute("SELECT pg_terminate_backend(%s)", [pid])

    stop = threading.Event()
    harel_worker.run(StopWhenIdle(runner.worker(worker_id="w"), stop), stop, idle_sleep=0)

    assert "harel worker iteration failed" in caplog.text
    assert Order.objects.get(pk=order.pk).status == "reserved"


def test_the_loop_survives_a_failing_iteration(caplog):
    class Broken:
        calls = 0

        def step(self):
            Broken.calls += 1
            if Broken.calls == 1:
                raise RuntimeError("database went away")
            stop.set()
            return False

        def fire_due_timers(self):
            return 0

    stop = threading.Event()
    harel_worker.run(Broken(), stop, idle_sleep=0)
    assert Broken.calls == 2
    assert "harel worker iteration failed" in caplog.text


def test_the_command_builds_its_worker_from_the_options(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        harel_worker, "run", lambda worker, stop, idle, **kw: seen.update(worker=worker, idle=idle, **kw)
    )
    call_command("harel_worker", "--worker-id", "w9", "--visibility", "12", "--idle-sleep", "0.5")

    assert seen["worker"].worker_id == "w9"
    assert seen["worker"].execution == "inline"
    assert seen["idle"] == 0.5
    assert seen["atomic"] is True and seen["using"] == "default"
