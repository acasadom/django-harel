# 4. The API

The publishing workflow runs in the background: the API only **records** what happened — a
new article, a submission, an approval — and the worker runs the machine. Each endpoint does
its work in one transaction, so what it records is all there or not there at all.

We use [django-ninja](https://django-ninja.dev) for the REST layer; any view works the same.

```bash
pip install django-ninja
```

## The router

```python
# articles/api.py
from django.db import transaction
from ninja import Router, Schema

from articles.models import Article

router = Router()


class ArticleIn(Schema):
    title: str
    body: str
    author: str


class RejectIn(Schema):
    note: str


@router.post("/")
def create_article(request, payload: ArticleIn):
    with transaction.atomic():
        article = Article.objects.create(title=payload.title, body=payload.body, author=payload.author)
    return {"article_id": article.pk, "execution_id": article.workflow.id}


@router.get("/{article_id}")
def get_article(request, article_id: int):
    article = Article.objects.get(pk=article_id)
    return {
        "article_id": article.pk,
        "status": article.status,
        "reviewer_note": article.reviewer_note,
        "published_at": article.published_at,
        "workflow": {"state": article.workflow.state, "outcome": article.workflow.outcome},
    }


@router.post("/{article_id}/submit")
def submit_article(request, article_id: int):
    Article.objects.get(pk=article_id).workflow.send("Submit")
    return {"queued": "Submit"}


@router.post("/{article_id}/approve")
def approve_article(request, article_id: int):
    Article.objects.get(pk=article_id).workflow.send("Approve")
    return {"queued": "Approve"}


@router.post("/{article_id}/reject")
def reject_article(request, article_id: int, payload: RejectIn):
    Article.objects.get(pk=article_id).workflow.send("Reject", note=payload.note)
    return {"queued": "Reject"}
```

`send()` never runs an action: it writes the event to the queue and returns. The worker takes
it from there.

## Why the transaction matters

```python
with transaction.atomic():
    article = Article.objects.create(...)
```

With `autostart`, creating the article also creates its execution and queues its `Start` —
**on the request's connection, inside its transaction**. If anything later in the block fails,
the article, the execution and the queued `Start` roll back together: no worker ever runs a
workflow for an article that doesn't exist. `send()` works the same way: an event sent inside
a transaction that rolls back was never sent.

That's the problem Celery leaves to you: a task sent with `.delay()` inside a transaction can
run before the transaction commits — or after it rolled back — unless you remember
`transaction.on_commit()`. Here the queue *is* the database, so the event is part of the
transaction.

## Incomplete events

`Reject { note: string }` declares what a `Reject` carries. `send()` checks it: a `Reject`
without its `note`, or with a number in it, raises `harel.EventError` (a `ValueError`) in the
view, before anything is queued — answer it as a bad request.

## Duplicate events

An event sent twice (a webhook retried by its sender, a double click) is processed once if it
carries the same id:

```python
order.workflow.send("PaymentSucceeded", event_id=stripe_event["id"], amount=amount)
```

## Without a model

`ExecutionField` is a convenience over two runners you can use directly — for an execution that
doesn't belong to one row:

```python
from django_harel import distributed_runner

exe = distributed_runner().create("publishing", context={"article_id": 42}, execution_id="nightly-42")
distributed_runner().send(exe.id, Event(kind="Submit"))
```

See [Runners](../reference/runners).

Next: [run the worker](05-worker).
