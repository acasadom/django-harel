"""DjangoTransport under concurrent transactions, on the databases with row locks."""

import threading

import pytest
from django.db import connection, transaction
from harel.spec.states import Event

from django_harel.transport import DjangoTransport

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _row_locks_only():
    if connection.vendor == "sqlite":
        pytest.skip("SQLite serializes writers: there is no concurrent transaction to race")


def _in_thread(fn):
    """Run `fn` on a thread with its own database connection; return (thread, errors)."""
    errors: list[BaseException] = []

    def run():
        try:
            fn()
        except BaseException as exc:  # surfaced by the test
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


def test_an_ack_does_not_drop_a_group_a_publish_is_still_writing_to():
    """A request publishes into G inside its transaction while a worker acks G's last message.
    The ack must wait for the request, see its message, and keep the group — else the message
    is left with no group, and no claim ever finds it."""
    transport = DjangoTransport()
    transport.publish("G", Event(kind="first"))
    lease = transport.claim("worker", 30)
    assert lease.event.kind == "first"

    published, release = threading.Event(), threading.Event()

    def request():
        with transaction.atomic():
            transport.publish("G", Event(kind="second"))
            published.set()
            release.wait(10)

    req, req_errors = _in_thread(request)
    assert published.wait(10)
    ack, ack_errors = _in_thread(lambda: transport.ack(lease))
    ack.join(0.5)
    assert ack.is_alive(), "the ack waits for the request holding the group"

    release.set()
    req.join(10)
    ack.join(10)
    assert not req_errors and not ack_errors

    second = transport.claim("worker", 30)
    assert second is not None and second.event.kind == "second"


def test_concurrent_claims_take_different_groups():
    transport = DjangoTransport()
    for group in ("A", "B"):
        transport.publish(group, Event(kind=group))

    holding, release = threading.Event(), threading.Event()
    leases: dict[str, object] = {}

    def first_claim():
        with transaction.atomic():
            leases["first"] = transport.claim("w1", 30)
            holding.set()
            release.wait(10)

    first, errors = _in_thread(first_claim)
    assert holding.wait(10)
    leases["second"] = transport.claim("w2", 30)  # skips the group locked by the first claim
    release.set()
    first.join(10)
    assert not errors

    assert {leases["first"].group_id, leases["second"].group_id} == {"A", "B"}
