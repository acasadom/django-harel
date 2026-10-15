from django.db import models

from django_harel import ExecutionField


class Order(models.Model):
    customer = models.CharField(max_length=255)
    items = models.JSONField(default=list)
    card_ok = models.BooleanField(default=True, help_text="Untick to have the card refused when charged.")
    status = models.CharField(max_length=32, default="placed")
    reminded = models.BooleanField(default=False)

    # shipped and billed side by side, by the workers; started with the row
    fulfilment = ExecutionField(
        "fulfilment", autostart=True, context=lambda order: {"order_id": order.pk, "items": order.items}
    )

    class Meta:
        app_label = "orders"

    def __str__(self):
        return f"order {self.pk} for {self.customer} [{self.status}]"


class Parcel(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="parcels")
    item = models.CharField(max_length=255)

    class Meta:
        app_label = "orders"
