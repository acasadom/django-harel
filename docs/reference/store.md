# DjangoStore

```python
from django_harel import DjangoStore

store = DjangoStore(using="default", trace_max=200, savepoints=True)
```

harel's `ExecutionStore` over Django's database: an execution's state, the events it emitted,
its timers and its children. `using` names the database (`DJANGO_HAREL["DATABASE"]` for the
built-in runners).

## Transactions

Every write runs in `transaction.atomic(using=...)`. Inside a caller's `atomic()` block that is
a savepoint, so the write commits or rolls back with the caller's; outside one, it is its own
transaction.

Each write is checked against the execution's `version`: if another writer advanced it since
it was loaded, the write raises `harel.StoreConflict` and changes nothing.

With `savepoints=False` the writes skip the savepoint, and a lost version check marks the
caller's transaction for rollback. That is for a transaction that is one unit of work and ends
on any failure — the worker's step. Leave it on anywhere else.

## Reading

| | |
|---|---|
| `load(execution_id)` | the `Execution`, or `None` |
| `list_executions(status=..., definition_id=..., roots_only=..., limit=..., cursor=...)` | a page of summaries; pass `next_cursor` back for the next |
| `read_trace(execution_id)` | the recorded steps, oldest first (with `TRACE` on) |

The rest of the interface is harel's `ExecutionStore` protocol, which the runners use.

## Schema

The tables are harel's schema version 3 (`harel.engine.schema`), created by django-harel's
migration. On SQLite and PostgreSQL they are the very tables harel's `SqliteStore` and
`PostgresStore` use (`create_schema=False`). On MySQL they keep the same columns, with
`VARCHAR` keys and a binary collation so ids are case-sensitive.
