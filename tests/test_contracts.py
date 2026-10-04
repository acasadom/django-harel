"""harel's own backend contracts (`harel.testing`), run on DjangoStore and DjangoTransport."""

import pytest
from django.db import connection
from harel.testing import (
    assert_listing_contract,
    assert_outbox_contract,
    assert_purge_contract,
    assert_transport_contract,
)
from harel.testing.purge import assert_ids_with_prefix_contract

from django_harel.schema import TABLES
from django_harel.store import DjangoStore
from django_harel.transport import DjangoTransport

pytestmark = pytest.mark.django_db(transaction=True)


def _empty_tables():
    with connection.cursor() as cur:
        for table in TABLES:
            cur.execute(f"DELETE FROM {table}")


def test_store_listing():
    assert_listing_contract(DjangoStore(), ordered=True, ns="ls-")


def test_store_outbox():
    assert_outbox_contract(DjangoStore(), ns="ob-")


def test_store_purge():
    assert_purge_contract(DjangoStore(), ns="pg-")


def test_store_ids_with_prefix():
    assert_ids_with_prefix_contract(DjangoStore(), ns="ip_%-")


def test_transport():
    def make(clock):
        _empty_tables()
        return DjangoTransport(clock=clock)

    assert_transport_contract(make)
