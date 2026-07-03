# 5. The worker

The `harel_worker` management command reads events from the queue and drives the state
machine transitions, calling your action functions along the way.

## Start the worker

```bash
python manage.py harel_worker
```

```
  Loaded definition: publishing from articles/machines/publishing.stm
Starting harel worker [hostname] concurrency=4 visibility=30.0s
```

The worker runs until you send SIGINT (Ctrl-C) or SIGTERM. It stops cleanly — in-flight
events are completed before the process exits.

## Run the full example

You need two terminals:

```bash
# terminal 1
python manage.py runserver

# terminal 2
python manage.py harel_worker
```

Then follow the publishing flow with curl (see `example/README.md` for the full sequence).

### Create an article

```bash
curl -s -X POST http://localhost:8000/api/articles/ \
  -H "Content-Type: application/json" \
  -d '{"title": "Why harel beats Celery", "body": "...", "author": "Alice"}'
```

The `Article` row and the harel `Execution` are created atomically. The machine starts in
`Draft`. Status is `draft` because `Draft` has no `on enter` action.

### Submit for review

```bash
curl -s -X POST http://localhost:8000/api/articles/1/submit
```

The API enqueues a `Submit` event and returns immediately. Watch terminal 2 — the worker
picks it up, transitions `Draft → InReview`, and `on_in_review` updates `Article.status`
to `"in_review"`.

### Approve

```bash
curl -s -X POST http://localhost:8000/api/articles/1/approve
curl -s http://localhost:8000/api/articles/1
```

The worker processes `Approve` and — because `Approved → Published` is an automatic
transition — both `on_approved` and `on_published` run in the same tick. Status becomes
`"published"` and `published_at` is set.

## How the worker finds your machines

The worker reads `DJANGO_HAREL["MACHINES"]` from your Django settings:

```python
DJANGO_HAREL = {
    "MACHINES": {
        "publishing": "articles/machines/publishing.stm",
    },
    "CONCURRENCY": 4,     # max events in flight simultaneously
    "VISIBILITY": 30.0,   # lease duration in seconds
}
```

Paths are relative to the directory where you run `manage.py`.

## Command-line options

All settings can be overridden at the command line:

```bash
python manage.py harel_worker \
  --concurrency 8 \
  --visibility 60 \
  --worker-id worker-1 \
  --trace
```

`--trace` enables per-execution timeline recording in `harel_trace`.

## Multiple workers

You can run multiple `harel_worker` processes in parallel. Each worker claims events
atomically (compare-and-swap on the `harel_queue` table) so there is no double processing.
With the default `DjangoTransport`, single-active-consumer semantics are enforced per
execution — only one worker processes one execution at a time.

## Transport

By default the event queue lives in the `harel_queue` database table — visible in Django
admin, zero extra infrastructure.

For higher throughput you can switch to Redis:

```python
DJANGO_HAREL = {
    "TRANSPORT": "redis",
    "TRANSPORT_URL": "redis://localhost:6379",
    "MACHINES": {...},
}
```

The trade-off: with Redis the queue is no longer in your database, so you lose Django
admin visibility of in-flight events. The store (`DjangoStore`) always stays in your
database regardless of transport choice — the atomicity guarantee between statechart state
and your domain models is in the store, not the transport.

See [Transport reference](../reference/transport) for details.
