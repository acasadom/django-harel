# django-harel

*Django integration for harel statecharts — a Celery alternative for state-machine-shaped work.*

[![CI](https://github.com/acasadom/django-harel/actions/workflows/ci.yml/badge.svg)](https://github.com/acasadom/django-harel/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/acasadom/django-harel/blob/main/LICENSE)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)

**django-harel** runs [harel](https://github.com/acasadom/harel) statecharts on Django's
database. You write a process as a `.stm` machine and its actions as plain functions that use
the ORM. Its state, its queue and its timers are rows in your database, next to your models.
It works with SQLite, PostgreSQL and MySQL, and needs no other infrastructure.

Requires Python 3.11+, Django 5.2+ and harel 0.10+. Alpha: the API may still change.

```
event Submit  {}
event Approve {}
event Reject  { note: string }

machine publishing {
  context { article_id: int }

  initial Draft

  state Draft    {}
  state InReview { on enter articles.actions.on_in_review }
  state Approved {}

  final Published success { on enter articles.actions.on_published }
  final Rejected  failure { on enter articles.actions.on_rejected }

  from Draft    to InReview on Submit
  from InReview to Approved on Approve
  from Approved to Published
  from InReview to Rejected on Reject
}
```

## Two ways to run a machine

**In the background**, like Celery, but with the coordination in the machine rather than in
chained tasks and status fields. The request records the event and answers at once. A worker
runs it. Waiting days for a webhook, retrying with back-off, timing out, fanning out and
joining are all part of the machine. A machine waiting for its next event is just a row.

**In the request**, for flows the user drives step by step: wizards, checkouts. The request
runs the event to the end and renders the new state. There is no queue and no worker.

## Transactions

Everything goes through the connection Django already has, so it joins your transactions:

```python
class Article(models.Model):
    ...
    publishing = ExecutionField("publishing", autostart=True)


with transaction.atomic():
    article = Article.objects.create(...)   # its publishing execution starts with it
```

If the block rolls back, the execution and its queued `Start` roll back with the article. No
worker ever runs a workflow for a row that doesn't exist, and you never need
`transaction.on_commit()`.

On the worker, each event is a transaction too: what its actions write, the machine's step and
the acknowledgement commit together. A worker that dies halfway leaves nothing behind.

## Install

```bash
pip install django-harel
```

```python
INSTALLED_APPS = [..., "django_harel"]

DJANGO_HAREL = {
    "MACHINES": {"publishing": "articles/machines/publishing.stm"},
}
```

```bash
python manage.py migrate
python manage.py harel_worker
```

## Use

```python
article.publishing.send("Submit")             # background: queued, the worker runs it
article.publishing.state                      # "InReview"

order.checkout.process("Pay", token=token)  # in the request: runs now, in this transaction
```

`ExecutionField` ties a machine to a model; the runners underneath (`distributed_runner()`,
`durable_runner()`) work for executions that don't belong to a row.

The Django admin's **Machines** catalog lists every machine: its diagram, what it invokes, its
top-level executions, and their counts by status. It also shows the queue by machine and every
worker's heartbeat. It flags work no live worker claims, a timer nothing is firing, and a worker
that is gone. From the executions list you can suspend, resume, cancel, terminate, redrive or
purge them.

Signals (`step_committed`, `execution_finished`, `execution_failed`) tell your code about every
step, once its transaction has committed.

## Example

[`example/`](https://github.com/acasadom/django-harel/tree/main/example) is a Django app with
a REST API (django-ninja) and a worker, on PostgreSQL with `docker compose up`. It has two machines:

- **articles**: the publishing workflow above, the one the tutorial builds.
- **orders**: shipping and billing in parallel regions, one parcel per item (a fan-out),
  payment timers, and a refused card to redrive from the admin.

See [`example/README.md`](https://github.com/acasadom/django-harel/blob/main/example/README.md).

## Documentation

[acasadom.github.io/django-harel](https://acasadom.github.io/django-harel/)

## License

Apache 2.0
