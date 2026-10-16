# The worker

```bash
python manage.py harel_worker [--worker-id ID] [--visibility SECONDS] [--idle-sleep SECONDS] [--machines M1,M2]
```

Runs the events the requests queued, one at a time, and fires the timers that fall due when
there is nothing else to do. Stops on SIGTERM or SIGINT after the event in hand.

| Option | Setting | Default | |
|---|---|---|---|
| `--worker-id` | — | `host:pid` | its name in the leases it holds |
| `--visibility` | `VISIBILITY` | `30` | seconds a claimed event stays leased before another worker may take it |
| `--idle-sleep` | `IDLE_SLEEP` | `0.2` | seconds to wait when the queue is empty and no timer is due |
| `--machines` | — | every machine | comma-separated machines: claim only the work of the executions started from them |
| — | `SUSPEND_RECHECK` | `5` | seconds before an event for a suspended execution is looked at again |
| — | `ATOMIC_STEP` | `True` | run each event in one transaction |

## Each event is a transaction

With `ATOMIC_STEP`, claiming an event, what its actions write, the machine's step and the
acknowledgement commit together. A worker that dies mid-event leaves nothing behind, and the
event is claimable again immediately.

If another writer moved the execution on while the event ran — a request that advanced it in
the meantime — the step's commit loses its version check, and the whole step rolls back too: its
actions wrote for a state that no longer holds. The event then runs again from the new state.
Inside that transaction the worker's store and queue write without savepoints, which saves a
quarter of its queries.

The transaction stays open while the actions run. On SQLite that holds the database's single
write lock for that long; keep actions short there, or set `ATOMIC_STEP = False` — then each
write commits on its own, and an event whose worker died comes back when its lease ends.

## Dedicating workers to machines

By default a worker claims every machine's queued work — the safety net every deployment should
keep running at least one of. `--machines publishing,fulfilment` restricts a worker to the
executions started from those machines, their whole tree included: regions, fan-out instances
and invoked submachines (`orders.parcels.parcel` is `fulfilment`'s own fan-out, so it's claimed
by whichever worker claims `fulfilment` — it can't be named on its own).

```
worker-fulfilment: python manage.py harel_worker --machines fulfilment
worker-everything-else: python manage.py harel_worker
```

This is additive capacity, not exclusivity: an unfiltered worker still claims a machine's work
even when it has dedicated workers too, so one slow or misconfigured `--machines` worker never
leaves a machine with nobody to run it. A name not in `DJANGO_HAREL['MACHINES']` is refused at
startup (`CommandError`), rather than silently running a worker that never claims anything.

The **Machines** catalog in the admin shows each machine's queue depth, so an unattended one
(a typo in `--machines`, or a machine nobody dedicated a worker to and the general worker can't
keep up with) is visible before it becomes an incident.

## Scaling

One process runs one event at a time; on PostgreSQL and MySQL, run more processes for more at
once. On SQLite, run one (see below). An execution's events run one at a time and in order
whatever the number of workers.

## Throughput

An event costs about fourteen short queries — claim, load, the actions' own writes, the commit,
the acknowledgement — so a worker's pace is set by the round-trip to the database and by how
fast it commits. Measured with `bench/run.py` (5,000 queued executions of a three-step machine,
every action checked to have run exactly once), on a laptop with the servers in Docker:

| Database | 1 worker | 4 workers | 8 workers |
|---|---|---|---|
| SQLite (local file) | ~1,170 events/s | ~1,110 | — |
| PostgreSQL 17 | ~200 | ~465 | ~605 |
| MySQL 8.4 | ~145 | ~275 | ~235 |

Take them as orders of magnitude, not promises: the benchmark ran on the host against servers
in Docker, so every query crossed Docker Desktop's port forwarding, and Docker's virtual disk
makes every commit slow — MySQL, which syncs two logs per commit by default, suffers most and
stops scaling first. A claim costs the same whatever the backlog. harel's own PostgreSQL
backends, measured the same way, run at about the same pace per worker (~210 events/s).

**On SQLite, run one worker process.** SQLite has one writer at a time, so more processes add
no throughput, and its lock isn't fair: with eight, the rate halves. harel measures the same of
its own SQLite backends.

## Deployment

Run it like any long-lived Django process — a systemd unit, a container, a Procfile line:

```
worker: python manage.py harel_worker
```

It keeps its database connection while there is work, and drops one that broke or outlived
`CONN_MAX_AGE` when it is idle. An error outside an action — the database unreachable — is
logged (logger `django_harel.worker`) and the worker carries on after `IDLE_SLEEP`, on a fresh
connection.
