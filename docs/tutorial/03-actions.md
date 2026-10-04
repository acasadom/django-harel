# 3. Actions

Actions are plain Python functions the machine calls as it enters or leaves a state — on the
worker for a machine run in the background, in the request for one run in the request. They
receive a `proxy` object and the `event` that triggered the transition, and they use the ORM
like any other Django code.

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

Each action gets a `proxy` with two attributes:

| Attribute | Type | Description |
|---|---|---|
| `proxy.execution_ctx` | `dict` | the execution's context: what `create(context=…)` passed, plus the machine's defaults |
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

## Transactions and failures

What an action writes to the database commits with the step that ran it: in the request,
with the request's transaction; on the worker, with the event's own transaction (see
[the worker](05-worker)). If the step doesn't commit, neither do the action's writes.

When an action **raises**, the machine decides what happens:

- a transition `on error` in scope takes it (the exception is in `context._error`) — model
  the failures you expect this way;
- otherwise the execution is **dead-lettered**: its status becomes `FAILED`, with the
  exception in `error`, and it waits for an operator (`redrive` it once the bug is fixed).

Either way, the step *commits* — the machine records the failure. So an action that writes
and then raises would leave its first writes behind. Make it all-or-nothing with Django's own
savepoints:

```python
from django.db import transaction

@transaction.atomic
def on_published(proxy, event, **_):
    ...
```

What an action does *outside* the database — an email, a payment API call — can't roll back.
A step can run more than once (a worker that dies mid-event, a redelivery), so pass
`proxy.idempotency_key` to the service: it is the same each time the same step runs.

Next: [build the API](04-api).
