"""The order API: place an order (its fulfilment starts with it), pay for it, look at it."""

from django.db import transaction
from ninja import Router, Schema

from orders.models import Order

router = Router()


class OrderIn(Schema):
    customer: str
    items: list[str]
    card_ok: bool = True


@router.post("/", summary="Place an order: shipping and billing start with it")
def place_order(request, payload: OrderIn):
    with transaction.atomic():
        order = Order.objects.create(customer=payload.customer, items=payload.items, card_ok=payload.card_ok)
    return {"order_id": order.pk, "execution_id": order.fulfilment.id}


@router.post("/{order_id}/pay", summary="The payment arrived: the worker charges the card")
def pay(request, order_id: int):
    Order.objects.get(pk=order_id).fulfilment.send("Paid")
    return {"queued": "Paid", "order_id": order_id}


@router.get("/{order_id}", summary="An order, its parcels and its fulfilment")
def get_order(request, order_id: int):
    order = Order.objects.get(pk=order_id)
    exe = order.fulfilment.execution
    return {
        "order_id": order.pk,
        "status": order.status,
        "reminded": order.reminded,
        "parcels": list(order.parcels.values_list("item", flat=True)),
        "fulfilment": {"status": exe.status.value, "outcome": exe.outcome} if exe else None,
    }
