# django-harel

*Django integration for harel statecharts — a Celery alternative for state-machine-shaped work.*

[![CI](https://github.com/acasadom/django-harel/actions/workflows/ci.yml/badge.svg)](https://github.com/acasadom/django-harel/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)

**django-harel** connects [harel](https://github.com/acasadom/harel) statecharts to Django.
You define your business process as a `.stm` state machine, wire action functions that touch
your Django models, and let the included worker drive everything — with the Django ORM as the
only infrastructure you need.

```
event Submit  {}
event Approve {}
event Reject  { note: string }

bind {
  on_in_review = myapp.actions.on_in_review
  on_published = myapp.actions.on_published
  on_rejected  = myapp.actions.on_rejected
}

machine publishing {
  initial Draft

  state Draft    {}
  state InReview { on enter on_in_review }
  state Approved {}

  final Published success  { on enter on_published }
  final Rejected  failure  { on enter on_rejected }

  from Draft    to InReview on Submit
  from InReview to Approved on Approve
  from Approved to Published
  from InReview to Rejected on Reject
}
```

## Why

When your domain is a set of named states with explicit transitions — an order, a
subscription, a content approval flow, a device lifecycle — a statechart is the right
model. django-harel gives you two things that other solutions don't:

**No dual-write.** When the worker commits a state transition, your action functions and
the statechart advance share a single `transaction.atomic()`. If an action raises, the
execution state does not advance and the event is redelivered. When you start an execution
from a request handler, the domain model creation and the statechart start commit together
or roll back together.

**No extra infrastructure by default.** The event queue lives in your existing database
(`harel_queue` table), visible in Django admin. Switch to Redis for higher throughput —
the store stays in your database, only the queue moves.

## Install

```bash
pip install django-harel
```

Add to `INSTALLED_APPS` and migrate:

```python
INSTALLED_APPS = [..., "django_harel"]
```

```bash
python manage.py migrate
```

## Quickstart

**1. Define a state machine** in a `.stm` file and point to it from settings:

```python
DJANGO_HAREL = {
    "MACHINES": {"publishing": "myapp/machines/publishing.stm"},
}
```

**2. Write action functions** that update your Django models:

```python
# myapp/actions.py
def on_published(proxy, event, **_):
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(
        status="published", published_at=timezone.now()
    )
```

**3. Start an execution** from your API — atomically with the domain model:

```python
with transaction.atomic():
    article = Article.objects.create(...)
    exe = runner.create("publishing", context={"article_id": article.pk})
    article.execution_id = exe.id
    article.save(update_fields=["execution_id"])
```

**4. Enqueue events** from your API endpoints:

```python
transport.publish(article.execution_id, Event(kind="Submit"))
```

**5. Run the worker** — it reads events and calls your actions:

```bash
python manage.py harel_worker
```

## Example

A complete article publishing app is in [`example/`](example/). It includes a
django-ninja REST API and the full publishing workflow (Draft → InReview → Published /
Rejected). See [`example/README.md`](example/README.md) for setup and curl commands.

## Documentation

Full documentation at [acasadom.github.io/django-harel](https://acasadom.github.io/django-harel/).

## License

Apache 2.0
