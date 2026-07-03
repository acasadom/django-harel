# harel_worker

`python manage.py harel_worker` runs a background worker that reads events from the
transport queue, drives statechart transitions, and calls your action functions.

## How it works

The worker is an async process built on harel's `AsyncWorker`. It:

1. Reads `DJANGO_HAREL["MACHINES"]` from settings and loads the `.stm` definitions.
2. Creates an `AsyncDjangoStore` (backed by your Django database) and an async-wrapped
   transport (Django or Redis, per `DJANGO_HAREL["TRANSPORT"]`).
3. Loops, claiming events from the queue, running transitions, and committing results.
4. Calls SIGTERM/SIGINT handlers so Ctrl-C stops the loop cleanly after in-flight events
   complete.

## Command-line options

```
python manage.py harel_worker [options]

  --concurrency N      Max events processed simultaneously (default: CONCURRENCY setting or 8)
  --visibility N       Lease duration in seconds (default: VISIBILITY setting or 30)
  --worker-id ID       Unique worker identifier (default: hostname)
  --trace              Enable per-execution trace recording in harel_trace
```

## Multiple workers

Running multiple `harel_worker` processes is safe. Each worker claims events atomically
and single-active-consumer semantics per execution are enforced by the transport. Scale
horizontally by adding more processes — no coordination needed.

## Graceful shutdown

The worker registers handlers for `SIGTERM` and `SIGINT`. When a signal is received it
sets a stop flag; the current in-flight events finish processing before the process exits.
Do not send `SIGKILL` unless you want events to be redelivered (they will be — at-least-once
delivery is guaranteed by the visibility lease expiry).

## Async internals

The worker uses `AsyncDjangoStore` and `AsyncDjangoTransport` — thin `sync_to_async`
wrappers over the sync Django ORM, running on a thread pool managed by asgiref. Action
functions are called via the same pool. If your actions use Django ORM they work as-is;
if they use async ORM (Django 4.1+) they also work — the pool handles both.

## Settings

See [Settings reference](settings) for the full `DJANGO_HAREL` configuration options.
