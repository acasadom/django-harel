# django-harel example — articles and orders

A small Django app with two workflows. The **API** records what happens — an article
submitted, an order paid — and a **worker** (`harel_worker`) runs the machines and their
actions, which write the app's models.

- **articles** — the publishing workflow the [tutorial](../docs/tutorial/01-installation.md)
  builds: Draft → InReview → Published / Rejected.
- **orders** — an order's fulfilment, to see the rest: shipping and billing side by side
  (orthogonal regions), one parcel per item (a fan-out `invoke`), a payment reminder and a
  give-up (timers), and a card refusal that dead-letters billing (for **Redrive**).

```
articles/machines/publishing.stm   orders/machines/fulfilment.stm, parcel.stm   ← the machines
articles/actions.py                orders/actions.py                            ← their actions
articles/api.py                    orders/api.py                                ← the REST API
articles/models.py                 orders/models.py                             ← the models (ExecutionField)
```

## Run it with Docker

```bash
cd example/
docker compose up
```

That starts PostgreSQL, the web app and a worker, migrates, creates the user `admin` (password
`admin`) and some demo data. Then open:

- **http://localhost:8000/admin/** — *Harel Statecharts › Machines* and *Executions*
- **http://localhost:8000/api/docs** — the API, to try it from the browser

The repository is mounted into the containers, and so is the harel checkout next to it
(`../../harel`, or set `HAREL_PATH`): edit either and reload the page; restart the worker
(`docker compose restart worker`) to have it pick the change up. `docker compose down -v`
throws the database away.

### What to look at

`seed_demo` leaves something in each situation:

| | |
|---|---|
| article "A draft", "Under review", "Published" | the publishing workflow at each of its steps |
| Dana's order | shipping done, billing **waiting for the payment** — a reminder timer is armed (after a minute the reminder fires, two minutes later the order is abandoned) |
| Eve's order | paid: both regions done, the order **DONE** |
| Frank's order | his card is refused: the billing region is **FAILED** |

To bring Frank's order back: in *Orders*, tick his **card ok** and save; in *Executions*, filter
on *failed*, select his billing region, run **Redrive**, pick
`Fulfilling.Billing.AwaitingPayment`; then pay again:

```bash
curl -X POST http://localhost:8000/api/orders/3/pay
```

The worker charges the card, billing finishes, and the order is fulfilled.

## Run it without Docker

On SQLite (`example.db`), from the repository's environment:

```bash
cd example/
uv run python manage.py migrate
uv run python manage.py createsuperuser   # to see the executions in /admin/
uv run python manage.py seed_demo         # optional: the demo data
```

## Run

You need **two terminals**:

```bash
# terminal 1 — API server
uv run python manage.py runserver

# terminal 2 — statechart worker
uv run python manage.py harel_worker
```

## Walk through the publishing flow

### Create an article

```bash
curl -s -X POST http://localhost:8000/api/articles/ \
  -H "Content-Type: application/json" \
  -d '{"title": "Why harel beats Celery", "body": "...", "author": "Alice"}'
```

```json
{"article_id": 1, "execution_id": "articles.article.publishing:1", "status": "draft"}
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
{"article_id": 1, "status": "draft", "publishing": {"status": "RUNNING", "state": "Draft", "outcome": null}, ...}
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
  "publishing": {"status": "DONE", "state": "Published", "outcome": "success"},
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

`http://localhost:8000/admin/` → **Harel Statecharts › Machines** lists each machine, and
**Executions** every instance with its state; the example records each step
(`"TRACE": True`), shown on an execution's page.

## Interactive API docs

django-ninja generates OpenAPI docs automatically:

```
http://localhost:8000/api/docs
```
