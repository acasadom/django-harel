# The worker

```bash
python manage.py harel_worker [--worker-id ID] [--visibility SECONDS] [--idle-sleep SECONDS]
```

Runs the events the requests queued, one at a time, and fires the timers that fall due when
there is nothing else to do. Stops on SIGTERM or SIGINT after the event in hand.

| Option | Setting | Default | |
|---|---|---|---|
| `--worker-id` | — | `host:pid` | its name in the leases it holds |
| `--visibility` | `VISIBILITY` | `30` | seconds a claimed event stays leased before another worker may take it |
| `--idle-sleep` | `IDLE_SLEEP` | `0.2` | seconds to wait when the queue is empty and no timer is due |
| — | `SUSPEND_RECHECK` | `5` | seconds before an event for a suspended execution is looked at again |
| — | `ATOMIC_STEP` | `True` | run each event in one transaction |

## Each event is a transaction

With `ATOMIC_STEP`, claiming an event, what its actions write, the machine's step and the
acknowledgement commit together. A worker that dies mid-event leaves nothing behind, and the
event is claimable again immediately.

The transaction stays open while the actions run. On SQLite that holds the database's single
write lock for that long; keep actions short there, or set `ATOMIC_STEP = False` — then each
write commits on its own, and an event whose worker died comes back when its lease ends.

## Scaling

One process runs one event at a time; run more processes for more at once. An execution's
events run one at a time and in order whatever the number of workers.

## Throughput

An event costs about twenty short queries — claim, load, the actions' own writes, the commit,
the acknowledgement — so a worker's pace is set by the round-trip to the database and by how
fast it commits. Measured with `bench/run.py` (10,000 queued executions of a three-step machine,
every action checked to have run exactly once), on a laptop with the servers in Docker:

| Database | 1 worker | 4 workers | 8 workers |
|---|---|---|---|
| SQLite (local file) | ~1,200 events/s | ~1,100 | ~550 |
| PostgreSQL 17 | ~130 | ~170–190 | ~400 |
| MySQL 8.4 | ~85 | ~45 | ~250 |
| MySQL 8.4, no binlog, `innodb_flush_log_at_trx_commit=2` | ~100 | ~260 | ~265 |

Take them as orders of magnitude, not promises: Docker's virtual disk makes every commit slow,
and MySQL, which syncs two logs per commit by default, suffers most from it. On SQLite more
workers don't help — one writer at a time. A claim costs the same whatever the backlog.

## Deployment

Run it like any long-lived Django process — a systemd unit, a container, a Procfile line:

```
worker: python manage.py harel_worker
```

It keeps its database connection while there is work, and drops one that broke or outlived
`CONN_MAX_AGE` when it is idle. An error outside an action — the database unreachable — is
logged (logger `django_harel.worker`) and the worker carries on after `IDLE_SLEEP`, on a fresh
connection.
