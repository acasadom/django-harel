# django-harel example — article publishing workflow

A minimal Django app that shows the two runtime modes of django-harel:

- **API** creates articles and enqueues editorial events (Submit, Approve, Reject).
- **Worker** (`harel_worker`) reads from the queue, runs the statechart transitions,
  and calls the action functions in `articles/actions.py` — which update `Article.status`,
  `Article.reviewer_note`, and `Article.published_at` in the database.

```
articles/machines/publishing.stm   ← the statechart (Draft → InReview → Published/Rejected)
articles/actions.py                ← Django ORM actions called by the worker
articles/api.py                    ← django-ninja REST endpoints
articles/models.py                 ← Article model
```

## Setup

```bash
cd example/
python manage.py migrate
python manage.py createsuperuser   # optional: to see the executions in /admin/
```

## Run

You need **two terminals**:

```bash
# terminal 1 — API server
python manage.py runserver

# terminal 2 — statechart worker
python manage.py harel_worker
```

## Walk through the publishing flow

### Create an article

```bash
curl -s -X POST http://localhost:8000/api/articles/ \
  -H "Content-Type: application/json" \
  -d '{"title": "Why harel beats Celery", "body": "...", "author": "Alice"}'
```

```json
{"article_id": 1, "execution_id": "article-1", "status": "draft"}
```

The `Article` row, the harel execution and its queued `Start` commit in **one transaction**.
The execution is `PENDING` until the worker runs its `Start` and takes it to `Draft`.

### Submit for editorial review

```bash
curl -s -X POST http://localhost:8000/api/articles/1/submit
```

```json
{"queued": "Submit", "article_id": 1}
```

The API **enqueues** a `Submit` event. Check the status immediately — still `draft`
until the worker has processed it:

```bash
curl -s http://localhost:8000/api/articles/1
```

```json
{"article_id": 1, "status": "draft", "workflow": {"status": "RUNNING", "state": "Draft", "outcome": null}, ...}
```

After the worker picks it up (watch terminal 2), the machine transitions
`Draft → InReview` and `on_in_review` sets `Article.status = "in_review"`:

```bash
curl -s http://localhost:8000/api/articles/1
```

```json
{"article_id": 1, "status": "in_review", ...}
```

### Approve — auto-publishes

```bash
curl -s -X POST http://localhost:8000/api/articles/1/approve
```

The worker processes `Approve`, transitions `InReview → Approved`, and because
`Approved → Published` is an **automatic transition** (no event required), both
`on_approved` and `on_published` run in the same step:

```bash
curl -s http://localhost:8000/api/articles/1
```

```json
{
  "article_id": 1,
  "status": "published",
  "published_at": "2026-10-04T13:43:49.959Z",
  "workflow": {"status": "DONE", "state": "Published", "outcome": "success"},
  ...
}
```

### Rejection path (second article)

```bash
# Create and submit
curl -s -X POST http://localhost:8000/api/articles/ \
  -H "Content-Type: application/json" \
  -d '{"title": "Draft with issues", "body": "...", "author": "Bob"}'

curl -s -X POST http://localhost:8000/api/articles/2/submit

# Reject with a reviewer note — note travels in event.data and is saved by on_rejected
curl -s -X POST http://localhost:8000/api/articles/2/reject \
  -H "Content-Type: application/json" \
  -d '{"note": "Needs more examples and a clearer conclusion."}'
```

```bash
curl -s http://localhost:8000/api/articles/2
```

```json
{
  "article_id": 2,
  "status": "rejected",
  "reviewer_note": "Needs more examples and a clearer conclusion.",
  "published_at": null
}
```

## The admin

`http://localhost:8000/admin/` → **Harel › Executions** lists every workflow with its state;
the example records each step (`"TRACE": True`), shown on an execution's page.

## Interactive API docs

django-ninja generates OpenAPI docs automatically:

```
http://localhost:8000/api/docs
```
