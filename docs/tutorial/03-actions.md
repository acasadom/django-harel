# 3. Actions

Actions are plain Python functions called by the worker when the machine enters a state.
They receive a `proxy` object and the `event` that triggered the transition.

## The Article model

```python
# articles/models.py
from django.db import models

class Article(models.Model):
    title        = models.CharField(max_length=255)
    body         = models.TextField()
    author       = models.CharField(max_length=255)
    status       = models.CharField(max_length=32, default="draft")
    reviewer_note = models.TextField(blank=True, default="")
    published_at  = models.DateTimeField(null=True, blank=True)
    execution_id  = models.CharField(max_length=64, null=True, blank=True)
```

`execution_id` links the Django model to the harel execution. `status`, `reviewer_note`,
and `published_at` are updated by the action functions below.

## Action functions

```python
# articles/actions.py
from django.utils import timezone
from articles.models import Article

def on_in_review(proxy, event, **_):
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(status="in_review")

def on_approved(proxy, event, **_):
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(status="approved")

def on_published(proxy, event, **_):
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(
        status="published",
        published_at=timezone.now(),
    )

def on_rejected(proxy, event, **_):
    note = event.data.get("note", "") if event else ""
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(
        status="rejected",
        reviewer_note=note,
    )

def on_archived(proxy, event, **_):
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(status="archived")
```

## The proxy object

The worker passes a `proxy` to each action with two attributes:

| Attribute | Type | Description |
|---|---|---|
| `proxy.execution_ctx` | `dict` | the context dict passed to `runner.create(context=…)` |
| `proxy.idempotency_key` | `str` | stable `{execution_id}:{version}:{index}` key for deduplicating external calls (emails, webhooks, etc.) |

`execution_ctx` is how actions reach back to the domain model. When you create an execution
you pass `context={"article_id": article.pk}`; every action for that execution can then do
`proxy.execution_ctx["article_id"]` to update the right row.

## Event data

Events can carry arbitrary data in `event.data` (a plain `dict`). The `Reject` event
declared as `event Reject { note: string }` in the DSL expects a `note` key. In Python
you publish it as:

```python
Event(kind="Reject", data={"note": "Needs more examples."})
```

And the action reads it:

```python
note = event.data.get("note", "")
```

## Atomicity

Actions run inside the worker's processing loop, which commits the execution advance and
all side effects in a single `transaction.atomic()`. If an action raises an exception the
entire commit is rolled back and the event is redelivered — your Django model update is
never persisted without the statechart also advancing.

Next: [build the API](04-api).
