# Transport

A transport is the queue that carries events between the API (publisher) and the worker
(consumer). django-harel ships with `DjangoTransport` and supports Redis via harel's
native `RedisTransport`.

## DjangoTransport (default)

Events are rows in the `harel_queue` table. Zero additional infrastructure — the same
database you already use for your app.

```python
from django_harel.transport import DjangoTransport

transport = DjangoTransport()
transport.publish("execution-id", Event(kind="Submit"))
```

### How claim works

`claim()` atomically locks the oldest available event from a group that has no other
in-flight event (single-active-consumer per execution). It uses a two-step
SELECT + conditional UPDATE:

```sql
-- 1. Find candidate
SELECT seq, group_id, payload FROM harel_queue
WHERE available_at <= now
  AND (locked_by IS NULL OR lock_expiry <= now)
  AND group_id NOT IN (
    SELECT group_id FROM (
      SELECT group_id FROM harel_queue
      WHERE locked_by IS NOT NULL AND lock_expiry > now
    ) AS _locked
  )
ORDER BY available_at, seq LIMIT 1

-- 2. CAS lock
UPDATE harel_queue SET locked_by = ?, lock_expiry = ?
WHERE seq = ?
  AND (locked_by IS NULL OR lock_expiry <= now)
  AND group_id NOT IN (...)
```

The subquery in the UPDATE is wrapped in a derived table alias so it runs correctly on
**SQLite, PostgreSQL, and MySQL/MariaDB**. If another worker claims the same row between
the SELECT and UPDATE, the rowcount is 0 and `None` is returned (the caller retries on
the next tick).

### Database compatibility

| Database | Supported |
|---|---|
| SQLite | ✓ |
| PostgreSQL | ✓ |
| MySQL / MariaDB | ✓ |
| Oracle | ✓ (not validated) |

## Redis transport

For higher-throughput deployments you can switch the queue to Redis:

```python
# settings.py
DJANGO_HAREL = {
    "TRANSPORT": "redis",
    "TRANSPORT_URL": "redis://localhost:6379",
    ...
}
```

Install the `redis` package: `pip install redis`.

**Trade-offs vs DjangoTransport:**

| | DjangoTransport | RedisTransport |
|---|---|---|
| Infrastructure | none (same DB) | Redis required |
| Queue visible in Django admin | ✓ | ✗ |
| Throughput | low–medium | high |
| Transport atomicity with store | no (separate systems) | no (separate systems) |

The store always stays as `DjangoStore` regardless of transport choice — execution state,
deduplication, timers, and spawns remain in your database. The transport is only the
queue: it does not participate in `transaction.atomic()` and never did. The atomicity
guarantee is between `DjangoStore` and your Django ORM calls, not between store and
transport.

## Configuration

Transport is selected via `DJANGO_HAREL["TRANSPORT"]`:

| Value | Transport | Extra setting required |
|---|---|---|
| `"django"` (default) | `DjangoTransport` | — |
| `"redis"` | `RedisTransport` | `TRANSPORT_URL` |

## Protocol methods

| Method | Description |
|---|---|
| `publish(group_id, event)` | Enqueue an event for an execution. |
| `claim(worker_id, visibility)` | Atomically lock one deliverable event. Returns a `Lease` or `None`. |
| `ack(lease)` | Delete the event (processing complete). |
| `nack(lease, delay)` | Release the event for redelivery, optionally after a delay. |
| `close()` | No-op for DjangoTransport; closes the Redis connection for RedisTransport. |
