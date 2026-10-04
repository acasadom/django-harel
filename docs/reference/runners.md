# Runners

django-harel builds harel's own runners over Django's database, once per process, from
`DJANGO_HAREL`. Both run in the caller's thread (harel's `execution="inline"`) on the
connection Django gives that thread, so every write joins the caller's transaction.

## `durable_runner()`

```python
from django_harel import durable_runner
```

A `harel.engine.durable.DurableRunner` over `DjangoStore`. Each call runs an event to the end,
actions included, in the caller's thread. For machines the request itself moves on — see
[A machine in the request](../tutorial/06-in-the-request).

| Method | Does |
|---|---|
| `create(definition_id, context=None, execution_id=None, priority=0)` | creates and starts an execution; returns it |
| `process(execution_id, event)` | runs one event; returns the execution |
| `cancel` / `terminate` / `suspend` / `resume` / `purge` / `redrive` | the control plane (below) |
| `store` | its `DjangoStore`, e.g. `store.load(execution_id)` |

`DJANGO_HAREL["ON_ACTION_ERROR"]` sets what an action that raises does: `"fail"` (the
default) dead-letters the execution; `"raise"` lets the exception reach the caller and doesn't
commit the step.

## `distributed_runner()`

```python
from django_harel import distributed_runner
```

A `harel.engine.distributed.DistributedRunner` over `DjangoStore` and `DjangoTransport`. It
never runs an action: it writes the execution and its events to the database and the workers
run them.

| Method | Does |
|---|---|
| `create(definition_id, context=None, execution_id=None, priority=0, start_on_create=True)` | creates an execution and queues its `Start` |
| `start(execution_id, data=None)` | queues the `Start` of one created with `start_on_create=False` |
| `send(execution_id, event)` | queues an event |
| `cancel` / `terminate` / `suspend` / `resume` / `purge` / `redrive` | the control plane (below) |
| `worker(...)` | a harel `Worker` over the same backends — what `harel_worker` runs |

`priority` (0–4) is the execution's: a worker started with harel's `high_ratio` serves the
higher ones first.

## The control plane

Operations on an execution as a whole rather than events for its machine. The admin offers
them as actions.

| | |
|---|---|
| `suspend(id)` / `resume(id)` | pause and continue; its queued events wait |
| `cancel(id, reason=None)` | runs the machine's `on Cancel` cleanup if it has one, else terminates |
| `terminate(id)` | ends it now, no cleanup |
| `purge(id, archive=None)` | deletes a finished execution and its children, after passing them to `archive` |
| `redrive(id, target_path)` | brings a `FAILED` execution back to `RUNNING` at a state you choose |

With `distributed_runner()` a cooperative cancel's cleanup runs on a worker; with
`durable_runner()` it runs in the caller.

## `definitions()`

```python
from django_harel import definitions
```

The machines in `DJANGO_HAREL["MACHINES"]`, loaded and validated once, by id. Both runners use
them.

## Building your own

The runners are harel's; the Django part is the backends. To build one with other options —
a fixed clock in a test, a `resolver` for `invoke`d machines loaded elsewhere:

```python
from harel.engine.durable import DurableRunner
from django_harel import DjangoStore, definitions

runner = DurableRunner(DjangoStore(), definitions(), execution="inline", on_action_error="raise")
```

`execution="inline"` is what keeps the runner on the caller's thread and transaction; harel's
default (`"background"`) runs on an event loop of its own, which Django's ORM can't be used
from.
