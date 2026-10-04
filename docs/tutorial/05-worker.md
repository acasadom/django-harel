# 5. The worker

`manage.py harel_worker` runs what the requests queued: it claims an event, runs the
machine's transition and its actions, and commits.

```bash
python manage.py harel_worker
```

```
harel worker myhost:4242: publishing
```

It runs until SIGTERM or Ctrl-C, and stops after the event in hand.

## Run the example

```bash
# terminal 1
python manage.py runserver

# terminal 2
python manage.py harel_worker
```

```bash
curl -s -X POST http://localhost:8000/api/articles/ \
  -H "Content-Type: application/json" \
  -d '{"title": "Why harel beats Celery", "body": "...", "author": "Alice"}'

curl -s http://localhost:8000/api/articles/1
```

Right after `create`, the workflow is `PENDING`: its `Start` is in the queue. A moment later
the worker has run it and the article is in `Draft`:

```json
{"article_id": 1, "status": "draft", "workflow": {"status": "RUNNING", "state": "Draft", "outcome": null}}
```

Submit and approve it:

```bash
curl -s -X POST http://localhost:8000/api/articles/1/submit
curl -s -X POST http://localhost:8000/api/articles/1/approve
curl -s http://localhost:8000/api/articles/1
```

The worker runs `Approve` — `InReview → Approved`, then straight on to `Published` (an
automatic transition) — and `on_approved` and `on_published` run in the same step:

```json
{"status": "published", "workflow": {"status": "DONE", "state": "Published", "outcome": "success"}}
```

## One event, one transaction

By default (`DJANGO_HAREL["ATOMIC_STEP"] = True`) the worker runs each event in a transaction
of its own: claiming it, what its actions write, the machine's step and the acknowledgement
commit together. If the worker dies halfway — killed, out of memory, the database gone — the
transaction rolls back: nothing the event did is left, and the event is claimable again at
once.

The trade-off is that the transaction stays open while the actions run. On PostgreSQL and
MySQL that only holds the rows involved. **On SQLite**, which has one writer at a time, it
holds the database's write lock — every other write, the requests' included, waits for the
step. Keep actions short there, or turn `ATOMIC_STEP` off: each write then commits on its
own, and an event a dead worker was holding comes back when its lease ends (`VISIBILITY`).

## Timers

A machine can wait with a `timeout`:

```
state AwaitingPayment { timeout 600 }
from AwaitingPayment to Abandoned on Timeout
```

The timer is a row in `harel_timers`. When the worker has no events to run it fires the timers
that are due — each becomes a `Timeout` event for its execution. That is all it takes for
retries with back-off, reminders, expiries, and periodic loops (a state that times out back
into itself): no cron, no beat scheduler.

```{important}
Timers need a worker running — also for machines you run in the request
(see [the next chapter](06-in-the-request)).
```

## More workers

Each process runs one event at a time. Run more of them to run more at once — on one machine
or many. Each execution's events still run one at a time and in order: a worker leases the
execution's queue while it works on it, and the others take other executions. On PostgreSQL
and MySQL workers claim in parallel (`SKIP LOCKED`); on SQLite they take turns on its write
lock, so on SQLite run a single worker.

## Options

```bash
python manage.py harel_worker --worker-id worker-1 --visibility 60 --idle-sleep 0.5
```

| Option | Default | |
|---|---|---|
| `--worker-id` | `host:pid` | its name in the leases it holds |
| `--visibility` | `VISIBILITY` (30) | seconds a claimed event stays leased; longer than your slowest action |
| `--idle-sleep` | `IDLE_SLEEP` (0.2) | seconds to wait when there is nothing to do |

Next: [run a machine in the request](06-in-the-request).
