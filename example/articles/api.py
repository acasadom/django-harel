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


@router.post("/", summary="Create article and start publishing workflow")
def create_article(request, payload: ArticleIn):
    """Creates the Article row and starts the statechart atomically.

    The initial state (Draft) has no action, but on_in_review will run
    later when the worker processes the Submit event.
    """
    runner = get_runner()
    with transaction.atomic():
        article = Article.objects.create(
            title=payload.title,
            body=payload.body,
            author=payload.author,
        )
        exe = runner.create("publishing", context={"article_id": article.pk})
        article.execution_id = exe.id
        article.save(update_fields=["execution_id"])
    article.refresh_from_db()
    return {"article_id": article.pk, "execution_id": exe.id, "status": article.status}


@router.get("/{article_id}", summary="Get article status")
def get_article(request, article_id: int):
    article = Article.objects.get(pk=article_id)
    return {
        "article_id": article.pk,
        "title": article.title,
        "author": article.author,
        "status": article.status,
        "reviewer_note": article.reviewer_note,
        "published_at": article.published_at,
    }


@router.post("/{article_id}/submit", summary="Submit for editorial review")
def submit_article(request, article_id: int):
    """Enqueues a Submit event — the worker will transition Draft → InReview
    and call on_in_review (updates Article.status to 'in_review')."""
    article = Article.objects.get(pk=article_id)
    get_transport().publish(article.execution_id, Event(kind="Submit"))
    return {"queued": "Submit", "article_id": article_id}


@router.post("/{article_id}/approve", summary="Approve article")
def approve_article(request, article_id: int):
    """Enqueues Approve — worker transitions InReview → Approved → Published
    (auto-transition), calling on_approved then on_published."""
    article = Article.objects.get(pk=article_id)
    get_transport().publish(article.execution_id, Event(kind="Approve"))
    return {"queued": "Approve", "article_id": article_id}


@router.post("/{article_id}/reject", summary="Reject article with a note")
def reject_article(request, article_id: int, payload: RejectIn):
    """Enqueues Reject with the reviewer's note in event.data.
    Worker transitions InReview → Rejected and on_rejected saves the note."""
    article = Article.objects.get(pk=article_id)
    get_transport().publish(
        article.execution_id,
        Event(kind="Reject", data={"note": payload.note}),
    )
    return {"queued": "Reject", "article_id": article_id}


@router.post("/{article_id}/archive", summary="Archive article")
def archive_article(request, article_id: int):
    article = Article.objects.get(pk=article_id)
    get_transport().publish(article.execution_id, Event(kind="Archive"))
    return {"queued": "Archive", "article_id": article_id}
