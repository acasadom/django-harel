# Django Admin

django-harel registers read-only admin views for all internal tables. Add
`django.contrib.admin` to `INSTALLED_APPS` to enable them.

## Registered models

| Model | Admin class | What you see |
|---|---|---|
| `HarelExecution` | `HarelExecutionAdmin` | All running and completed executions with status, active state, version, and parent |
| `HarelQueue` | `HarelQueueAdmin` | Events currently in the queue, with lock status and expiry |
| `HarelOutbox` | `HarelOutboxAdmin` | Events pending relay after a commit |
| `HarelTimer` | `HarelTimerAdmin` | Scheduled timeouts and their fire times |
| `HarelTrace` | `HarelTraceAdmin` | Execution timeline entries (only populated when `--trace` is on) |

All admins are **read-only** — no create, edit, or delete from the UI. The statechart
engine is the only writer; manual edits would corrupt the CAS versioning.

## HarelExecution list view

The execution list shows denormalized columns so you can scan and filter without
expanding the JSON blob:

- `id` — the execution UUID
- `definition_id` — which machine this is (e.g. `"publishing"`)
- `status` — `RUNNING`, `DONE`, `FAILED`, `CANCELLED`
- `active_path` — the current state name (e.g. `"InReview"`)
- `version` — how many events have been processed
- `parent_id` — set for child executions spawned by orthogonal regions

Use the `status` and `definition_id` filters in the sidebar to narrow down executions.

## Enabling the admin

```python
# settings.py
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    ...
    "django_harel",
]
```

The admin classes are registered automatically when `django_harel` is in
`INSTALLED_APPS` — no extra configuration needed.
