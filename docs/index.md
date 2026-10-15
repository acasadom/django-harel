# django-harel

*Django integration for harel statecharts — a Celery alternative for state-machine-shaped work.*

**django-harel** runs [harel](https://github.com/acasadom/harel) statecharts on Django's
database. You write a process as a `.stm` machine, its actions as plain functions that use
the ORM, and the machine's state, its queue and its timers live in your database, next to
your models, in tables created by `migrate`. SQLite, PostgreSQL and MySQL are supported.

## Two ways to run a machine

**In the request.** The request runs the event to the end — the transition and its actions —
and answers with the machine's new state. There is no queue and no worker. This suits flows
whose state the user moves on: a wizard, a checkout, an approval screen. Everything the step
writes commits with the request's own transaction, or rolls back with it.

**In the background.** The request only *records* the event — in its own transaction, so a
rolled-back request never sent it — and answers at once. A worker
(`manage.py harel_worker`) runs it later. This is the Celery case: long-running processes,
retries with back-off, waiting days for a webhook or a timeout, fan-out and join, periodic
loops. A machine waiting for its next event is a row in the database; it costs nothing.

The two share the same tables, so one execution can use both: a wizard driven in the request
that hands its heavy work to the workers when the user confirms.

## When it fits — and when it doesn't

Use it when the work has **states**: an order, a subscription, a document review, a device's
lifecycle, a payment waiting for its webhook. The `.stm` file is then the single place that
says what happens next, instead of status fields, chained tasks and cron jobs.

It is not a replacement for a one-line `send_email.delay()`: a one-state machine for a
fire-and-forget call is more ceremony than it is worth.

```{toctree}
:maxdepth: 1
:caption: Tutorial

tutorial/01-installation
tutorial/02-state-machine
tutorial/03-actions
tutorial/04-api
tutorial/05-worker
tutorial/06-in-the-request
```

```{toctree}
:maxdepth: 1
:caption: Reference

reference/fields
reference/runners
reference/worker
reference/store
reference/transport
reference/admin
reference/signals
reference/settings
reference/testing
```
