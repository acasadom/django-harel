"""The fulfilment machine's actions, run by the worker."""

from orders.models import Order, Parcel


class CardRefused(Exception):
    pass


def pack(stm, event, **_):
    Parcel.objects.create(order_id=stm.execution_ctx["order_id"], item=stm.execution_ctx["item"])


def remind(stm, event, **_):
    Order.objects.filter(pk=stm.execution_ctx["order_id"]).update(reminded=True)


def charge(stm, event, **_):
    # a payment provider call in real life; here the order says whether the card works.
    # Not modelled with `on error`: a refusal dead-letters the billing region, for an operator
    order = Order.objects.get(pk=stm.execution_ctx["order_id"])
    if not order.card_ok:
        raise CardRefused(f"the card of order {order.pk} was refused")
    Order.objects.filter(pk=order.pk).update(status="paid")


def mark_fulfilled(stm, event, **_):
    Order.objects.filter(pk=stm.execution_ctx["order_id"]).update(status="fulfilled")


def mark_abandoned(stm, event, **_):
    Order.objects.filter(pk=stm.execution_ctx["order_id"]).update(status="abandoned")
