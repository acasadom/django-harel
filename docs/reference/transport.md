# DjangoTransport

```python
from django_harel import DjangoTransport

transport = DjangoTransport(using="default")
```

harel's `Transport` — the queue the workers read — over Django's database, in the tables
`harel_transport_messages` and `harel_transport_groups`.

## In the caller's transaction

`publish()` writes on the caller's connection, so an event queued inside `atomic()` is sent
only if the block commits. The transport says so to harel (`shares_store_transaction`), which
then lets a failed publish reach the caller instead of retrying it later.

## Delivery

Messages are grouped by execution. A group's messages are delivered in order, one at a time:
a worker leases the group while it handles its head, and other workers take other groups.
Groups are served round-robin — the one served longest ago first — among those at or above
the claim's `min_priority`.

- **SQLite** keeps the lease on the message (harel's SQLite layout); claims take turns on
  SQLite's write lock.
- **PostgreSQL and MySQL** keep it on the group (harel's PostgreSQL layout) and claim with
  `SELECT … FOR UPDATE SKIP LOCKED`, so workers claim different groups in parallel. `publish`
  and `ack` both lock the group's row, so the `ack` that empties a group can't delete it while
  another transaction is still publishing into it.

A lease lasts `visibility` seconds. A worker that dies holding one releases it when it ends —
or at once, when the worker's step was a transaction that rolled back.
