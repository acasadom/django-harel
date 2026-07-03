# DjangoStore

`DjangoStore` implements the harel `ExecutionStore` protocol using Django's ORM. It is
the component that persists statechart state to your database.

## The killer feature: transaction participation

`DjangoStore.commit()` wraps every write in `transaction.atomic()`. Django nests
`atomic()` blocks using SAVEPOINTs, so if there is already an active transaction,
the statechart advance joins it automatically.

This matters in two places:

**In the worker** — when the worker processes an event, your action functions and the
statechart advance run inside the same `transaction.atomic()`. If an action raises,
the execution state does not advance and the event is redelivered:

```python
# articles/actions.py — called by the worker
def on_published(proxy, event, **_):
    # This UPDATE and the statechart advance commit together.
    # If this raises, the execution stays in its previous state.
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(
        status="published", published_at=timezone.now()
    )
```

**In the request handler (DjangoRunner)** — when you create an execution from an API
view, you can wrap the domain model creation and the execution start in one `atomic()`:

```python
with transaction.atomic():
    order = Order.objects.create(user=user)
    exe = runner.create("checkout", context={"order_id": order.pk})
    # Both commit together — or both roll back.
```

Note: the transport (event queue) is a separate concern and does **not** share this
transaction. The atomicity guarantee is between `DjangoStore` and your Django ORM calls.

## Usage

`DjangoStore` is instantiated automatically by `DjangoRunner`. You rarely need to create
one directly:

```python
from django_harel.store import DjangoStore

store = DjangoStore()
exe = store.load("some-execution-id")
```

## Constructor

```python
DjangoStore(trace_max: int = 200)
```

`trace_max` is the ring-buffer size for the execution trace (only used when tracing is
enabled). Older entries are pruned automatically.

## Protocol methods

| Method | Description |
|---|---|
| `load(execution_id)` | Load an `Execution` by id. Returns `None` if not found. |
| `save(exe)` | Persist an execution (delegates to `commit`). |
| `commit(exe, emits, ...)` | Atomic advance: CAS on `version`, outbox, dedup, timers, spawns, trace. |
| `is_processed(execution_id, event_id)` | Deduplication check. |
| `pending_outbox()` / `ack_outbox(seq)` | Outbox relay. |
| `pending_spawns()` / `ack_spawn(seq)` | Orthogonal region spawn relay. |
| `due_timers(now)` / `delete_timer(...)` | Timer sweep. |
| `read_trace(execution_id)` | Read the execution timeline. |
| `list_executions(...)` | Paginated list of execution summaries. |
| `close()` | No-op — Django manages its own connection pool. |

## CAS (optimistic concurrency)

Every execution has a `version` integer. `commit()` does:

```sql
UPDATE harel_executions SET version = old+1, ... WHERE id = ? AND version = old
```

If another worker advanced the same execution between load and commit, the UPDATE matches
zero rows and `StoreConflict` is raised. The worker retries automatically.

## Database compatibility

`DjangoStore` uses the Django ORM exclusively — no raw SQL. It works with every
Django-supported database: SQLite, PostgreSQL, MySQL/MariaDB, Oracle.
