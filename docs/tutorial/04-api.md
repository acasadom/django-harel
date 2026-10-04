# 4. The API

The publishing workflow runs in the background: the API only **records** what happened — a
new article, a submission, an approval — and the worker runs the machine. Each endpoint does
its work in one transaction, so what it records is all there or not there at all.

We use [django-ninja](https://django-ninja.dev) for the REST layer; any view works the same.

```bash
pip install django-ninja
```

## The runner

`django_harel.distributed_runner()` is the runner that hands work to the workers. It is built
once per process from your settings, on first use:

```python
from django_harel import distributed_runner

runner = distributed_runner()
runner.create("publishing", context={...})     # a new execution; its Start goes to the queue
runner.send(execution_id, Event(kind="Submit"))  # an event for the queue
```

Neither call runs any action: they write to the database, in the caller's transaction, and
return.

## The router

```python
# articles/api.py
from django.db import transaction
from harel.spec.states import Event
from ninja import Router, Schema

from articles.models import Article
from django_harel import distributed_runner

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
        exe = distributed_runner().create(
            "publishing", context={"article_id": article.pk}, execution_id=f"article-{article.pk}"
        )
        article.execution_id = exe.id
        article.save(update_fields=["execution_id"])
    return {"article_id": article.pk, "execution_id": exe.id, "status": article.status}


@router.get("/{article_id}")
def get_article(request, article_id: int):
    article = Article.objects.get(pk=article_id)
    exe = distributed_runner().store.load(article.execution_id)
    return {
        "article_id": article.pk,
        "status": article.status,
        "reviewer_note": article.reviewer_note,
        "published_at": article.published_at,
        "workflow": {"status": exe.status.value, "state": exe.active_path, "outcome": exe.outcome},
    }


def _send(article_id: int, event: Event) -> dict:
    article = Article.objects.get(pk=article_id)
    distributed_runner().send(article.execution_id, event)
    return {"queued": event.kind, "article_id": article_id}


@router.post("/{article_id}/submit")
def submit_article(request, article_id: int):
    return _send(article_id, Event(kind="Submit"))


@router.post("/{article_id}/approve")
def approve_article(request, article_id: int):
    return _send(article_id, Event(kind="Approve"))


@router.post("/{article_id}/reject")
def reject_article(request, article_id: int, payload: RejectIn):
    return _send(article_id, Event(kind="Reject", data={"note": payload.note}))
```

## Why the transaction matters

```python
with transaction.atomic():
    article = Article.objects.create(...)
    exe = distributed_runner().create("publishing", context={"article_id": article.pk}, ...)
    ...
```

`create()` writes the execution and queues its `Start` **on the request's connection**,
inside its transaction. If anything later in the block fails, the article, the execution and
the queued `Start` roll back together: no worker ever runs a workflow for an article that
doesn't exist.

That's the problem Celery leaves to you: a task sent with `.delay()` inside a transaction can
run before the transaction commits — or after it rolled back — unless you remember
`transaction.on_commit()`. Here the queue *is* the database, so the event is part of the
transaction.

## Choosing the execution id

`execution_id=f"article-{article.pk}"` names the execution after the domain object it drives.
Any string unique to it works — an order number, a Stripe PaymentIntent id. A second
`create()` with the same id raises `ExecutionAlreadyExists`, which makes "start this workflow
once" safe to retry.

## Incomplete events

`Reject { note: string }` declares what a `Reject` carries. `send()` checks it: a `Reject`
without its `note`, or with a number in it, raises `harel.EventError` (a `ValueError`) in the
view, before anything is queued — answer it as a bad request.

## Duplicate events

An event sent twice (a webhook retried by its sender, a double click) is processed once if it
carries the same id:

```python
Event(kind="PaymentSucceeded", id=stripe_event["id"], data={...})
```

Next: [run the worker](05-worker).
