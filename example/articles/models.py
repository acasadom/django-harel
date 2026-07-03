from django.db import models


class Article(models.Model):
    title = models.CharField(max_length=255)
    body = models.TextField()
    author = models.CharField(max_length=255)
    status = models.CharField(max_length=32, default="draft")
    reviewer_note = models.TextField(blank=True, default="")
    published_at = models.DateTimeField(null=True, blank=True)
    execution_id = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        app_label = "articles"

    def __str__(self):
        return f'"{self.title}" by {self.author} [{self.status}]'
