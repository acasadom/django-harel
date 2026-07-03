"""Statechart actions for the publishing machine.

Called by the harel worker when entering a state. Each function receives:
  proxy.execution_ctx  — dict passed to runner.create(context=...)
  event                — the Event that triggered the transition (or None on auto-transition)
  proxy.idempotency_key — stable key for deduplicating external calls (emails, webhooks, etc.)
"""

from django.utils import timezone

from articles.models import Article


def on_in_review(proxy, event, **_):
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(status="in_review")


def on_approved(proxy, event, **_):
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(status="approved")


def on_published(proxy, event, **_):
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(
        status="published",
        published_at=timezone.now(),
    )


def on_rejected(proxy, event, **_):
    note = event.data.get("note", "") if event else ""
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(
        status="rejected",
        reviewer_note=note,
    )


def on_archived(proxy, event, **_):
    Article.objects.filter(pk=proxy.execution_ctx["article_id"]).update(status="archived")
