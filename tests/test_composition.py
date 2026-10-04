"""A machine with orthogonal regions and a fan-out invoke, end to end on Django's database.

fulfilment.stm forks two regions — Shipping and Billing — and Shipping invokes one
parcel.stm per item: the root, its 2 regions and N parcels are each an execution, created
through the store's spawn outbox and joined back through its event outbox.
"""

import pytest
from harel.engine.execution import Status
from harel.spec.states import Event

from django_harel import conf
from tests.app.models import Order, Parcel

pytestmark = pytest.mark.django_db(transaction=True)


def _drain(worker) -> int:
    """Run the worker until the queue is empty; return how many events it ran."""
    ran = 0
    while worker.step():
        ran += 1
    return ran


def _tree(store, root_id: str) -> list:
    return [store.load(i) for i in sorted(store.ids_with_prefix(root_id))]


def _start(runner, items: list[str]):
    order = Order.objects.create(item="kit", qty=len(items))
    exe = runner.create(
        "fulfilment", context={"order_id": order.pk, "items": items}, execution_id=f"f-{order.pk}"
    )
    return order, exe


def test_regions_and_fan_out_through_the_worker():
    runner = conf.distributed_runner()
    worker = runner.worker(worker_id="w")
    order, exe = _start(runner, ["a", "b", "c"])

    assert _drain(worker) > 0
    assert sorted(Parcel.objects.filter(order=order).values_list("item", flat=True)) == ["a", "b", "c"]
    tree = _tree(runner.store, exe.id)
    assert len(tree) == 1 + 2 + 3  # the root, its two regions, a parcel per item
    root = runner.store.load(exe.id)
    assert root.status is Status.RUNNING, "billing still waits for Paid"

    runner.send(exe.id, Event(kind="Paid"))
    _drain(worker)

    root = runner.store.load(exe.id)
    assert root.status is Status.DONE and root.outcome == "success"
    assert Order.objects.get(pk=order.pk).status == "fulfilled"
    assert all(e.status is Status.DONE for e in _tree(runner.store, exe.id))
    assert runner.store.pending_outbox() == [] and runner.store.pending_spawns() == []

    # the whole tree is purged with its root
    assert runner.purge(exe.id)
    assert runner.store.ids_with_prefix(exe.id) == []


def test_a_failed_parcel_fails_the_join():
    runner = conf.distributed_runner()
    worker = runner.worker(worker_id="w")
    order, exe = _start(runner, ["a", "broken"])

    _drain(worker)
    runner.send(exe.id, Event(kind="Paid"))
    _drain(worker)

    root = runner.store.load(exe.id)
    assert root.status is Status.DONE and root.outcome == "failed"
    assert Order.objects.get(pk=order.pk).status == "unfulfilled"
    assert list(Parcel.objects.filter(order=order).values_list("item", flat=True)) == ["a"]


def test_regions_and_fan_out_in_the_request():
    runner = conf.durable_runner()
    order, exe = _start(runner, ["a", "b"])

    assert Parcel.objects.filter(order=order).count() == 2
    assert runner.store.load(exe.id).status is Status.RUNNING

    root = runner.process(exe.id, Event(kind="Paid"))
    assert root.status is Status.DONE and root.outcome == "success"
    assert Order.objects.get(pk=order.pk).status == "fulfilled"
