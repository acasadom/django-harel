"""manage.py harel_worker."""

import threading

import pytest
from django.core.management import CommandError, call_command
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


def test_a_lost_cas_rolls_the_whole_step_back(caplog):
    """Between the worker's load and its commit another writer moves the execution on. The
    step's actions have already written; its commit loses the CAS. All of it must roll back —
    the actions' writes included — and the event run again: the stock is taken once."""
    from harel.engine.distributed import DistributedRunner

    from django_harel.store import DjangoStore
    from django_harel.transport import DjangoTransport

    class InterferedOnce(DjangoStore):
        interfered = False

        def load_for_event(self, execution_id, event_id):
            loaded = super().load_for_event(execution_id, event_id)
            if not InterferedOnce.interfered:
                InterferedOnce.interfered = True
                other = DjangoStore()  # another writer commits a step first
                moved = other.load(execution_id)
                other.save(moved)
            return loaded

    Stock.objects.create(item="widget", available=5)
    order = Order.objects.create(item="widget", qty=2)
    conf.distributed_runner().create("order", context={"order_id": order.pk}, execution_id="cas")

    # the worker's backends, as `conf.worker_runner(atomic_step=True)` builds them
    runner = DistributedRunner(
        InterferedOnce(savepoints=False),
        DjangoTransport(savepoints=False),
        conf.definitions(),
        execution="inline",
    )
    stop = threading.Event()
    worker = CrashAfterStep(runner.worker(worker_id="w"), stop)
    worker.ran = 1  # count the events, without crashing on the first
    harel_worker.run(worker, stop, idle_sleep=0, atomic=True)

    assert InterferedOnce.interfered
    assert worker.ran == 3, "the Start ran twice: once rolled back, once for good"
    assert "harel worker iteration failed" not in caplog.text
    assert Stock.objects.get(pk="widget").available == 3
    assert Order.objects.get(pk=order.pk).status == "reserved"
    assert conf.distributed_runner().store.load("cas").active_path.endswith("AwaitingPayment")


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
    assert seen["worker"].store.savepoints is False  # its own transaction per event
    assert seen["worker"].execution == "inline"
    assert seen["idle"] == 0.5
    assert seen["atomic"] is True and seen["using"] == "default"
    assert seen["worker"]._inline.machines is None  # unset: every machine


def test_machines_restricts_which_machines_the_worker_claims(monkeypatch):
    seen = {}
    monkeypatch.setattr(harel_worker, "run", lambda worker, stop, idle, **kw: seen.update(worker=worker))
    call_command("harel_worker", "--machines", "order,fulfilment")

    assert seen["worker"]._inline.machines == frozenset({"order", "fulfilment"})


def test_machines_rejects_a_name_outside_django_harels_machines():
    with pytest.raises(CommandError, match="pizza"):
        call_command("harel_worker", "--machines", "order,pizza")


def test_the_worker_publishes_its_machine_versions_at_startup(monkeypatch):
    from django_harel.store import DjangoStore

    monkeypatch.setattr(harel_worker, "run", lambda *a, **kw: None)
    assert DjangoStore().load_definition("order", 1) is None

    call_command("harel_worker")

    record = DjangoStore().load_definition("order", 1)
    assert record is not None and record.definition_id == "order"


def test_the_worker_refuses_a_machine_changed_without_a_new_version(monkeypatch):
    from harel.engine.store.base import DefinitionRecord

    from django_harel.store import DjangoStore

    monkeypatch.setattr(harel_worker, "run", lambda *a, **kw: None)
    DjangoStore().publish_definition(DefinitionRecord("order", 1, "not-the-real-digest", {}, None))

    with pytest.raises(CommandError, match="order"):
        call_command("harel_worker")
