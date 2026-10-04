import json

from django.db import models


class HarelExecution(models.Model):
    """A row of harel's `harel_executions` table, for the admin.

    The schema is harel's and its migration creates it (`managed = False`); harel writes the
    rows through `DjangoStore` or its own backends. `data` is the whole Execution as JSON —
    status, outcome, active state and context live inside it."""

    id = models.CharField(max_length=255, primary_key=True)
    definition_id = models.CharField(max_length=255)
    data = models.TextField()
    version = models.IntegerField()

    class Meta:
        app_label = "django_harel"
        db_table = "harel_executions"
        managed = False
        verbose_name = "execution"

    def __str__(self) -> str:
        return self.id

    @property
    def snapshot(self) -> dict:
        return json.loads(self.data)
