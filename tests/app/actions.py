"""The order machine's actions: they write the test app's own tables."""

from django.db.models import F

from tests.app.models import Order, Stock


class OutOfStock(Exception):
    pass


def reserve_stock(stm, event, **_):
    order = Order.objects.get(pk=stm.execution_ctx["order_id"])
    taken = Stock.objects.filter(item=order.item, available__gte=order.qty).update(
        available=F("available") - order.qty
    )
    if not taken:
        raise OutOfStock(f"not enough {order.item} for {order.qty}")
    Order.objects.filter(pk=order.pk).update(status="reserved")


def mark_paid(stm, event, **_):
    Order.objects.filter(pk=stm.execution_ctx["order_id"]).update(status="paid")


def mark_expired(stm, event, **_):
    Order.objects.filter(pk=stm.execution_ctx["order_id"]).update(status="expired")


class Damaged(Exception):
    pass


def pack(stm, event, **_):
    from tests.app.models import Parcel

    item = stm.execution_ctx["item"]
    if item == "broken":
        raise Damaged(item)
    Parcel.objects.create(order_id=stm.execution_ctx["order_id"], item=item)


def mark_fulfilled(stm, event, **_):
    Order.objects.filter(pk=stm.execution_ctx["order_id"]).update(status="fulfilled")


def mark_unfulfilled(stm, event, **_):
    Order.objects.filter(pk=stm.execution_ctx["order_id"]).update(status="unfulfilled")


def tick(stm, event, *, step: str, **_):
    from tests.app.models import Tick

    Tick.objects.create(execution_id=stm.execution_ctx["id"], step=step)
