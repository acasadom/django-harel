# 4. The API

The REST API has two responsibilities:

1. **Create** — create the `Article` row and start the harel execution atomically.
2. **Enqueue** — publish events to the queue. The worker processes them; the API returns
   immediately without waiting.

We use [django-ninja](https://django-ninja.dev) for the REST layer.

## Install django-ninja

```bash
pip install django-ninja
```

## Lazy runner and transport

Define factory functions that are called on first use — not at import time. This avoids
issues during Django startup and makes testing easier:

```python
# articles/services.py
from functools import lru_cache

@lru_cache(maxsize=1)
def get_runner():
    from pathlib import Path
    from django.conf import settings
    from django_harel import DjangoRunner
    from harel.dsl import definition_from_dsl_file

    machines = settings.DJANGO_HAREL.get("MACHINES", {})
    return DjangoRunner(definitions={
        defn_id: definition_from_dsl_file(Path(path))
        for defn_id, path in machines.items()
    })

def get_transport():
    from django_harel.transport import DjangoTransport
    return DjangoTransport()
```

`get_runner()` is cached with `@lru_cache` because loading `.stm` files from disk is
a one-time cost. `get_transport()` is stateless so no caching is needed.

## The router

```python
# articles/api.py
from django.db import transaction
from harel.spec.states import Event
from ninja import Router, Schema

from articles.models import Article
from articles.services import get_runner, get_transport

router = Router()

class ArticleIn(Schema):
    title: str
    body: str
    author: str

class RejectIn(Schema):
    note: str

@router.post("/")
def create_article(request, payload: ArticleIn):
    runner = get_runner()
    with transaction.atomic():
        article = Article.objects.create(
            title=payload.title, body=payload.body, author=payload.author,
        )
        exe = runner.create("publishing", context={"article_id": article.pk})
        article.execution_id = exe.id
        article.save(update_fields=["execution_id"])
    article.refresh_from_db()
    return {"article_id": article.pk, "execution_id": exe.id, "status": article.status}

@router.get("/{article_id}")
def get_article(request, article_id: int):
    article = Article.objects.get(pk=article_id)
    return {
        "article_id": article.pk, "title": article.title, "author": article.author,
        "status": article.status, "reviewer_note": article.reviewer_note,
        "published_at": article.published_at,
    }

@router.post("/{article_id}/submit")
def submit_article(request, article_id: int):
    article = Article.objects.get(pk=article_id)
    get_transport().publish(article.execution_id, Event(kind="Submit"))
    return {"queued": "Submit"}

@router.post("/{article_id}/approve")
def approve_article(request, article_id: int):
    article = Article.objects.get(pk=article_id)
    get_transport().publish(article.execution_id, Event(kind="Approve"))
    return {"queued": "Approve"}

@router.post("/{article_id}/reject")
def reject_article(request, article_id: int, payload: RejectIn):
    article = Article.objects.get(pk=article_id)
    get_transport().publish(
        article.execution_id,
        Event(kind="Reject", data={"note": payload.note}),
    )
    return {"queued": "Reject"}

@router.post("/{article_id}/archive")
def archive_article(request, article_id: int):
    article = Article.objects.get(pk=article_id)
    get_transport().publish(article.execution_id, Event(kind="Archive"))
    return {"queued": "Archive"}
```

## Wire the router

```python
# api.py  (project root)
from ninja import NinjaAPI
from articles.api import router as articles_router

api = NinjaAPI()
api.add_router("/articles", articles_router)
```

```python
# urls.py
from django.urls import path
from api import api

urlpatterns = [path("api/", api.urls)]
```

## The create endpoint in detail

```python
with transaction.atomic():
    article = Article.objects.create(...)
    exe = runner.create("publishing", context={"article_id": article.pk})
    article.execution_id = exe.id
    article.save(update_fields=["execution_id"])
```

`runner.create()` starts the machine synchronously in the current thread. The machine
enters `Draft` (no action there), and the execution is persisted via `DjangoStore`. Because
this all happens inside `transaction.atomic()`, the `Article` row and the `harel_executions`
row are written together — or rolled back together if anything fails.

The subsequent `submit`, `approve`, `reject`, and `archive` endpoints only call
`transport.publish()`. They return immediately — the worker handles the rest.

Next: [run the worker](05-worker).
