from django.db import models

from django_harel.workflow import Workflow


class Stock(models.Model):
    item = models.CharField(max_length=64, primary_key=True)
    available = models.IntegerField()


class Order(models.Model):
    item = models.CharField(max_length=64)
    qty = models.IntegerField()
    status = models.CharField(max_length=32, default="placed")


class Parcel(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    item = models.CharField(max_length=64)


class Tick(models.Model):
    """One row per action run of the bench machine: a duplicate run breaks the unique key."""

    execution_id = models.CharField(max_length=255)
    step = models.CharField(max_length=32)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["execution_id", "step"], name="tick_once")]


class Ticket(models.Model):
    """Two workflows on one model: one the workers run, started with the row; one the request
    runs, started by hand."""

    title = models.CharField(max_length=64)

    workflow = Workflow("job", autostart=True)
    review = Workflow("job", run="request", context=lambda ticket: {"title": ticket.title})
