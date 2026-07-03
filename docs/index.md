# django-harel

*Django integration for harel statecharts — a Celery alternative for state-machine-shaped work.*

[![CI](https://github.com/acasadom/django-harel/actions/workflows/ci.yml/badge.svg)](https://github.com/acasadom/django-harel/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)

**django-harel** connects [harel](https://github.com/acasadom/harel) statecharts to Django.
You define your business process as a `.stm` state machine, wire action functions that touch
your Django models, and let the included worker drive everything — with the Django ORM as the
only infrastructure you need.

## When to use it

Use django-harel when your domain is a **named set of states with explicit transitions** — an
order, a subscription, a content approval flow, a device lifecycle — and you want:

- **No dual-write**: when the worker commits a state transition, it uses `DjangoStore`
  which wraps the write in `transaction.atomic()`. Your action functions run inside that
  same transaction — statechart state and domain model updates commit together or not at all.
- **No extra infrastructure by default**: the event queue lives in your existing database
  (`harel_queue` table), visible in Django admin. Switch to Redis for higher throughput —
  at the cost of losing queue visibility in the admin.
- **Background processing**: a `manage.py harel_worker` command reads events from the queue
  and drives the machine asynchronously, calling your action functions.

## Tutorial

The tutorial builds the article publishing app in `example/` step by step, from a blank
Django project to a running API + worker.

```{toctree}
:maxdepth: 1
:caption: Tutorial

tutorial/01-installation
tutorial/02-state-machine
tutorial/03-actions
tutorial/04-api
tutorial/05-worker
```

## Reference

```{toctree}
:maxdepth: 1
:caption: Reference

reference/store
reference/transport
reference/runner
reference/worker
reference/admin
reference/settings
```
