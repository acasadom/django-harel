"""The article API. Every endpoint only *records* what happened — the article row, its
publishing execution, the event — in one transaction; the worker (`manage.py harel_worker`)
runs the machine and its actions."""

from django.db import transaction
from ninja import Router, Schema

from articles.models import Article

router = Router()


class ArticleIn(Schema):
    title: str
    body: str
    author: str


class RejectIn(Schema):
    note: str


@router.post("/", summary="Create an article and start its publishing execution")
def create_article(request, payload: ArticleIn):
    """The row and its publishing execution's queued Start commit together (`autostart`): if
    anything here fails, there is no article and no execution."""
    with transaction.atomic():
        article = Article.objects.create(title=payload.title, body=payload.body, author=payload.author)
    return {"article_id": article.pk, "execution_id": article.publishing.id, "status": article.status}


@router.get("/{article_id}", summary="Get an article and where its publishing is")
def get_article(request, article_id: int):
    article = Article.objects.get(pk=article_id)
    exe = article.publishing.execution
    return {
        "article_id": article.pk,
        "title": article.title,
        "author": article.author,
        "status": article.status,
        "reviewer_note": article.reviewer_note,
        "published_at": article.published_at,
        "publishing": {"status": exe.status.value, "state": exe.active_path, "outcome": exe.outcome},
    }


@router.post("/{article_id}/submit", summary="Submit for editorial review")
def submit_article(request, article_id: int):
    Article.objects.get(pk=article_id).publishing.send("Submit")
    return {"queued": "Submit", "article_id": article_id}


@router.post("/{article_id}/approve", summary="Approve: the worker publishes it")
def approve_article(request, article_id: int):
    Article.objects.get(pk=article_id).publishing.send("Approve")
    return {"queued": "Approve", "article_id": article_id}


@router.post("/{article_id}/reject", summary="Reject with a note")
def reject_article(request, article_id: int, payload: RejectIn):
    Article.objects.get(pk=article_id).publishing.send("Reject", note=payload.note)
    return {"queued": "Reject", "article_id": article_id}


@router.post("/{article_id}/archive", summary="Archive")
def archive_article(request, article_id: int):
    Article.objects.get(pk=article_id).publishing.send("Archive")
    return {"queued": "Archive", "article_id": article_id}
