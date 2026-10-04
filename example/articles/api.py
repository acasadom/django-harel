"""The article API. Every endpoint only *records* what happened — the article row, the
execution, the event — in one transaction; the worker (`manage.py harel_worker`) runs the
machine and its actions."""

from django.db import transaction
from harel.spec.states import Event
from ninja import Router, Schema

from articles.models import Article
from django_harel import distributed_runner

router = Router()


class ArticleIn(Schema):
    title: str
    body: str
    author: str


class RejectIn(Schema):
    note: str


@router.post("/", summary="Create an article and start its publishing workflow")
def create_article(request, payload: ArticleIn):
    """The Article row, the execution and its queued Start commit together: if anything here
    fails, there is no article and no workflow."""
    with transaction.atomic():
        article = Article.objects.create(title=payload.title, body=payload.body, author=payload.author)
        exe = distributed_runner().create(
            "publishing", context={"article_id": article.pk}, execution_id=f"article-{article.pk}"
        )
        article.execution_id = exe.id
        article.save(update_fields=["execution_id"])
    return {"article_id": article.pk, "execution_id": exe.id, "status": article.status}


@router.get("/{article_id}", summary="Get an article and where its workflow is")
def get_article(request, article_id: int):
    article = Article.objects.get(pk=article_id)
    exe = distributed_runner().store.load(article.execution_id)
    return {
        "article_id": article.pk,
        "title": article.title,
        "author": article.author,
        "status": article.status,
        "reviewer_note": article.reviewer_note,
        "published_at": article.published_at,
        "workflow": {"status": exe.status.value, "state": exe.active_path, "outcome": exe.outcome},
    }


def _send(article_id: int, event: Event) -> dict:
    article = Article.objects.get(pk=article_id)
    distributed_runner().send(article.execution_id, event)
    return {"queued": event.kind, "article_id": article_id}


@router.post("/{article_id}/submit", summary="Submit for editorial review")
def submit_article(request, article_id: int):
    return _send(article_id, Event(kind="Submit"))


@router.post("/{article_id}/approve", summary="Approve: the worker publishes it")
def approve_article(request, article_id: int):
    return _send(article_id, Event(kind="Approve"))


@router.post("/{article_id}/reject", summary="Reject with a note")
def reject_article(request, article_id: int, payload: RejectIn):
    return _send(article_id, Event(kind="Reject", data={"note": payload.note}))


@router.post("/{article_id}/archive", summary="Archive")
def archive_article(request, article_id: int):
    return _send(article_id, Event(kind="Archive"))
