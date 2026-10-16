"""manage.py seed_demo — articles and orders in every situation the admin can show.

Queues their events; a running `harel_worker` takes them from there. Does nothing if there are
orders already (so it can run on every start).
"""

from articles.models import Article
from django.core.management.base import BaseCommand
from django.db import transaction

from orders.models import Order


class Command(BaseCommand):
    help = "Create demo articles and orders for the admin."

    def handle(self, *args, **options):
        if Order.objects.exists():
            self.stdout.write("demo data already there")
            return
        with transaction.atomic():
            draft = Article.objects.create(title="A draft", body="...", author="Alice")
            in_review = Article.objects.create(title="Under review", body="...", author="Bob")
            in_review.publishing.send("Submit")
            published = Article.objects.create(title="Published", body="...", author="Carol")
            published.publishing.send("Submit")
            published.publishing.send("Approve")

            # waits for its payment: a reminder after a minute, abandoned two minutes later
            Order.objects.create(customer="Dana", items=["book", "lamp"])
            # paid: shipped and billed, done
            paid = Order.objects.create(customer="Eve", items=["chair"])
            paid.fulfilment.send("Paid")
            # its card is refused: the billing region is dead-lettered (FAILED) — tick its
            # card in Orders, then redrive the region from Executions, and pay again
            refused = Order.objects.create(customer="Frank", items=["desk", "pen", "mug"], card_ok=False)
            refused.fulfilment.send("Paid")
        self.stdout.write(
            f"created articles {draft.pk}, {in_review.pk}, {published.pk} and orders "
            f"for Dana, Eve and Frank (Frank's card is refused)"
        )
        self.stdout.write(
            self.style.WARNING(
                "\nheads up: the worker log will shortly show a traceback for order "
                f"{refused.pk} (Frank) — CardRefused, unhandled error; failing execution "
                "...Fulfilling.Billing:0. That's intentional, not a bug: his card is refused "
                "on purpose, to dead-letter that region for the Redrive demo (see "
                "example/README.md). The worker itself keeps running; every other order "
                "processes normally.\n"
            )
        )
