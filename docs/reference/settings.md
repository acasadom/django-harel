# Settings

Everything lives in one `DJANGO_HAREL` dict:

```python
DJANGO_HAREL = {
    "MACHINES": {
        "publishing": "articles/machines/publishing.stm",
        "checkout": "shop/machines/checkout.stm",
    },
    "DATABASE": "default",
    "TRACE": False,
    "ON_ACTION_ERROR": "fail",
    "VISIBILITY": 30.0,
    "SUSPEND_RECHECK": 5.0,
    "IDLE_SLEEP": 0.2,
    "ATOMIC_STEP": True,
    "HEARTBEAT": 10.0,
    "MERMAID_JS": "https://cdn.jsdelivr.net/npm/mermaid@11.15.0/dist/mermaid.min.js",
}
```

| Key | Default | |
|---|---|---|
| `MACHINES` | — (required) | machine name → `.stm` file; a relative path is relative to `BASE_DIR` |
| `DATABASE` | `"default"` | the database alias harel's tables are in |
| `TRACE` | `False` | record each execution's steps (shown in the admin) |
| `ON_ACTION_ERROR` | `"fail"` | `durable_runner()`: `"fail"` dead-letters, `"raise"` raises to the caller |
| `VISIBILITY` | `30.0` | worker: seconds a claimed event stays leased |
| `SUSPEND_RECHECK` | `5.0` | worker: seconds before a suspended execution's event is looked at again |
| `IDLE_SLEEP` | `0.2` | worker: seconds to wait when there's nothing to do |
| `ATOMIC_STEP` | `True` | worker: one transaction per event (see [the worker](worker)) |
| `HEARTBEAT` | `10.0` | worker: seconds between the heartbeats it records in `harel_workers` |
| `MERMAID_JS` | Mermaid 11.15 on jsDelivr | admin: the script that draws the machines (see [the admin](admin)) |

The machines are loaded and validated once per process, on first use. Changing
`DJANGO_HAREL` with `override_settings` reloads them.

## SQLite

A worker and a web server on one SQLite file both write to it. Let them wait for each other
instead of failing with "database is locked":

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
    }
}
```
