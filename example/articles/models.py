from django.db import models

from django_harel import ExecutionField


class Article(models.Model):
    title = models.CharField(max_length=255)
    body = models.TextField()
    author = models.CharField(max_length=255)
    status = models.CharField(max_length=32, default="draft")
    reviewer_note = models.TextField(blank=True, default="")
    published_at = models.DateTimeField(null=True, blank=True)

    # named after its own machine: started with the row, in the transaction that saves it
    publishing = ExecutionField("publishing", autostart=True)

    class Meta:
        app_label = "articles"

    def __str__(self):
        return f'"{self.title}" by {self.author} [{self.status}]'
