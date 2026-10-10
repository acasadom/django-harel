# 1. Installation

## Requirements

- Python 3.11+
- Django 5.2 or later
- SQLite, PostgreSQL, or MySQL 8.0.1+

## Install

```bash
pip install django-harel
```

## Add to your project

```python
# settings.py
INSTALLED_APPS = [
    ...
    "django_harel",
]
```

```bash
python manage.py migrate
```

## Minimal configuration

`DJANGO_HAREL["MACHINES"]` maps each machine's name to its `.stm` file. A relative path is
relative to `settings.BASE_DIR` (or, without one, to the working directory):

```python
# settings.py
DJANGO_HAREL = {
    "MACHINES": {
        "publishing": "articles/machines/publishing.stm",
    },
}
```

The other settings are in the [settings reference](../reference/settings).

## What `migrate` created

harel's own schema, with every table prefixed `harel_`:

| Table | Holds |
|---|---|
| `harel_executions` | one row per execution: its state, context and status, as JSON |
| `harel_outbox` | events a step emitted, until they are delivered |
| `harel_processed_events` | the events each execution handled (a redelivered one is skipped) |
| `harel_timers` | armed timeouts and when they fire |
| `harel_spawns` | child executions a step is about to create |
| `harel_trace` | each execution's recent steps, when tracing is on |
| `harel_transport_messages` | the queue the workers read |
| `harel_transport_groups` | one row per execution with queued events: its lease and priority |

On SQLite and PostgreSQL these are exactly the tables harel's own backends create, so harel's
tools can read them too.

One table is django-harel's own, outside harel's schema: `django_harel_queue_log` records when
each queued message was published, for the admin's Machines page's "oldest waiting" — harel's own
schema has no timestamp on a message.

```{note}
If your test suite uses `TransactionTestCase` (or `pytest.mark.django_db(transaction=True)`),
Django's flush between tests leaves these tables alone — no model of yours owns them. See
[Testing](../reference/testing).
```

Next: [define the state machine](02-state-machine).
