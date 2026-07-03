# DjangoRunner

`DjangoRunner` is a synchronous, in-thread statechart runner for use inside Django views
and API endpoints. It participates in `transaction.atomic()` — advancing the statechart
and saving domain models happen in the same transaction.

## When to use it

Use `DjangoRunner` when you need to **start** a new execution from a request handler:

```python
with transaction.atomic():
    order = Order.objects.create(user=user)
    exe = runner.create("checkout", context={"order_id": order.pk})
    order.execution_id = exe.id
    order.save(update_fields=["execution_id"])
```

For subsequent events — things that happen asynchronously, triggered by external systems
or after a delay — **publish to the transport** and let the worker handle them. Do not
call `runner.process()` from a request handler for worker-driven events.

## Constructor

```python
DjangoRunner(
    definitions: dict[str, Definition],
    store: Optional[DjangoStore] = None,
    clock: Callable[[], float] = time.time,
    resolver: Optional[MachineResolver] = None,
    trace: bool = False,
)
```

`definitions` maps machine name to a loaded `Definition` (from `definition_from_dsl_file`).

## Lazy initialization

Load definitions once, not at import time:

```python
# services.py
from functools import lru_cache

@lru_cache(maxsize=1)
def get_runner():
    from pathlib import Path
    from django.conf import settings
    from django_harel import DjangoRunner
    from harel.dsl import definition_from_dsl_file

    machines = settings.DJANGO_HAREL.get("MACHINES", {})
    return DjangoRunner(definitions={
        name: definition_from_dsl_file(Path(path))
        for name, path in machines.items()
    })
```

## Methods

### `create(definition_id, context=None) → Execution`

Creates and starts a new execution. The machine enters its initial state and runs any
`on enter` actions for that state synchronously in the current thread.

```python
exe = runner.create("publishing", context={"article_id": article.pk})
```

The returned `Execution` is already persisted. Its `id` is what you store as
`execution_id` on your domain model.

### `process(execution_id, event) → Execution`

Loads an execution, feeds it one event, commits the result. Used when driving the
machine synchronously from a request (less common — the worker drives most events).

```python
exe = runner.process(exe.id, Event(kind="Submit"))
```

### `fire_due_timers() → int`

Delivers all timers that have passed their fire time. Returns the number fired. Call
this from a periodic task if you use `timeout` in your `.stm` files.

### `recover(definition_id) → None`

Drains the durable outbox for a given machine (relays emitted events after a restart).

## Under the hood

`DjangoRunner` wraps harel's internal `_SyncDriver`, which runs the engine synchronously
in the calling thread. It does **not** use anyio or a BlockingPortal — that is what keeps
it compatible with Django's synchronous view layer and with `transaction.atomic()`.
