# ExecutionField

```python
from django_harel import ExecutionField


class Article(models.Model):
    ...
    workflow = ExecutionField("publishing", autostart=True)
```

A model's machine, one execution per row — declared like a `Field`, and what you get back per
row (a `BoundExecution`) works like a `BoundField` does: `ExecutionField` is the class-level
configuration, `article.workflow` is the row's own handle onto its execution. The execution's
id comes from the model, the attribute and the primary key — `articles.article.workflow:42` —
so the model needs no column for it.

## Options

| | Default | |
|---|---|---|
| `definition_id` | — | the machine, as named in `DJANGO_HAREL["MACHINES"]` |
| `autostart` | `False` | start the execution when a row is created, in the transaction that saves it |
| `run` | `"background"` | how `start()` runs: `"background"` queues the `Start` for the workers; `"request"` runs it now |
| `context` | `{"<model>_id": pk}` | a function of the row giving the new execution's context |
| `execution_id` | `"<app>.<model>.<attribute>:<pk>"` | a function of the row giving the execution's id |

`autostart` doesn't fire for rows saved without `post_save` (`bulk_create`) or loaded from
fixtures (`loaddata`).

## On a row

`article.workflow` is a `BoundExecution`:

| | |
|---|---|
| `id` | the execution's id |
| `execution` | the execution as stored now, or `None` before it starts — read on each access |
| `exists`, `status`, `state`, `outcome` | shortcuts into `execution` (`state` is the active state's path) |
| `start(priority=0, **context)` | creates the execution, with `context` added to the field's |
| `send(kind, event_id=None, **data)` | queues an event for the workers, in the current transaction |
| `process(kind, event_id=None, **data)` | runs an event now, in this thread and transaction; returns the execution |

`event_id` makes an event idempotent: a second one with the same id is ignored — pass the id
your source gives it (a webhook's event id).

Several `ExecutionField`s on one model each get their own execution — name each attribute
after what it tracks:

```python
class Ticket(models.Model):
    support = ExecutionField("support", autostart=True)
    review = ExecutionField("review", run="request")
```

## From an execution to its row

```python
from django_harel import instance_for
from django_harel.signals import execution_finished


@receiver(execution_finished)
def notify_author(sender, step, execution, **kwargs):
    article = instance_for(execution)  # the Article, or None
    ...
```

`instance_for` finds the row of an execution an `ExecutionField` started with its default id,
and returns `None` for any other.
