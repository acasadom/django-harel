# Settings reference

All django-harel settings live in a single `DJANGO_HAREL` dict in your `settings.py`.

```python
DJANGO_HAREL = {
    "MACHINES": {
        "publishing": "articles/machines/publishing.stm",
        "checkout":   "orders/machines/checkout.stm",
    },
    "TRANSPORT":    "django",
    "TRANSPORT_URL": "redis://localhost:6379",
    "CONCURRENCY":  8,
    "VISIBILITY":   30.0,
    "WORKER_ID":    "worker-1",
}
```

## Keys

### `MACHINES` *(required for the worker)*

```python
"MACHINES": {"machine_name": "path/to/machine.stm", ...}
```

A dict mapping machine names to `.stm` file paths. Paths are relative to the directory
where you run `manage.py`. The worker loads every machine listed here at startup.

### `TRANSPORT`

```python
"TRANSPORT": "django"   # default
"TRANSPORT": "redis"
```

Selects the event queue backend. `"django"` uses the `harel_queue` database table
(default, no extra dependencies). `"redis"` uses harel's `RedisTransport` — requires the
`redis` package and `TRANSPORT_URL`.

### `TRANSPORT_URL`

```python
"TRANSPORT_URL": "redis://localhost:6379"
```

Connection URL for the Redis transport. Only used when `TRANSPORT` is `"redis"`.

### `CONCURRENCY`

```python
"CONCURRENCY": 8   # default
```

Maximum number of events the worker processes simultaneously. Each event runs in a
`sync_to_async` thread. Tune based on your database connection pool size.

### `VISIBILITY`

```python
"VISIBILITY": 30.0   # default, seconds
```

How long the worker holds a lease on a claimed event. If the worker crashes before
`ack()`-ing the event, it becomes available again after this window. Set it longer than
your slowest action.

### `WORKER_ID`

```python
"WORKER_ID": "worker-1"   # default: socket.gethostname()
```

A unique identifier for this worker process, stored in `locked_by` on claimed events.
Defaults to the machine hostname. Set explicitly when running multiple workers on the
same host.
