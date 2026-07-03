# 1. Installation

## Requirements

- Python 3.11+
- Django 4.2 or later
- An existing Django project

## Install

```bash
pip install django-harel
```

For Redis transport (optional, for higher-throughput deployments):

```bash
pip install django-harel redis
```

## Add to your project

Add `django_harel` to `INSTALLED_APPS` and run the migrations that create the harel
tables (`harel_executions`, `harel_queue`, and five supporting tables):

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

Add a `DJANGO_HAREL` dict to your settings. At minimum you need `MACHINES` — a mapping
of machine names to `.stm` file paths (relative to the directory where you run `manage.py`):

```python
# settings.py
DJANGO_HAREL = {
    "MACHINES": {
        "publishing": "articles/machines/publishing.stm",
    },
}
```

The full list of settings is in the [Settings reference](../reference/settings).

## What was just created

`migrate` creates seven tables in your database, all prefixed with `harel_`:

| Table | Purpose |
|---|---|
| `harel_executions` | one row per running state machine instance |
| `harel_queue` | event queue consumed by `harel_worker` |
| `harel_outbox` | emitted events pending relay |
| `harel_processed_events` | deduplication log |
| `harel_timers` | scheduled timeouts |
| `harel_spawns` | orthogonal region child-creation intents |
| `harel_trace` | optional execution timeline (ring buffer) |

Your application code only touches `harel_queue` (to publish events) and reads
`harel_executions` indirectly through `DjangoStore`. The rest is internal to the engine.

Next: [define the state machine](02-state-machine).
