# Signals

```python
from django.dispatch import receiver
from django_harel.signals import execution_failed


@receiver(execution_failed)
def page_someone(sender, step, execution, **kwargs):
    alert(f"{execution.definition_id} {execution.id} failed: {execution.error}")
```

| Signal | Sent for |
|---|---|
| `step_committed` | every committed step |
| `execution_finished` | a step that took an execution to `DONE` or `CANCELLED` |
| `execution_failed` | a step that took it to `FAILED` — an action raised and no `on error` took it |

Each is sent with `sender=HarelExecution` and two arguments:

- `step` — harel's `Step`: `cause` (`"create"`, `"start"`, `"event"` or `"control"`),
  `from_status`/`to_status`, `from_path`/`to_path`, `event_kind`/`event_id`, the `actions` it
  ran, and `command` for a control-plane one;
- `execution` — the execution as that step committed it.

To listen to one machine only, check `execution.definition_id`.

## Only what committed

A step is part of a transaction — the request's, or the worker's own for the event — and its
signals are sent **when that transaction commits**, through `transaction.on_commit`. A step that
rolls back sends nothing: a view that fails after advancing a machine, a worker that dies
mid-event, a worker step that lost to another writer and runs again. Every signal stands for
something that is in the database.

A receiver that raises is logged (logger `django_harel.signals`); the step stays committed, and
the request or the worker carries on.

## Cost

With no receiver connected, nothing is prepared. With one, each commit keeps a copy of the
execution until its transaction commits. A receiver runs in the request or the worker that
committed the step, after its transaction: keep it short, or hand the slow part off.
