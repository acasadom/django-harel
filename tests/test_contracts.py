"""harel's own backend contracts (`harel.testing`), run on DjangoStore and DjangoTransport."""

import pytest
from django.db import connection
from harel.testing import (
    assert_definitions_contract,
    assert_listing_contract,
    assert_machine_routing_contract,
    assert_outbox_contract,
    assert_purge_contract,
    assert_transport_contract,
)
from harel.testing.purge import assert_ids_with_prefix_contract

from django_harel.schema import ALL_TABLES
from django_harel.store import DjangoStore
from django_harel.transport import DjangoTransport

pytestmark = pytest.mark.django_db(transaction=True)


def _empty_tables():
    with connection.cursor() as cur:
        for table in ALL_TABLES:
            cur.execute(f"DELETE FROM {table}")


# with savepoints (the requests) and without (the worker, inside its own transaction per event)
savepoints = pytest.mark.parametrize("savepoints", [True, False], ids=["savepoints", "no-savepoints"])


@savepoints
def test_store_listing(savepoints):
    assert_listing_contract(DjangoStore(savepoints=savepoints), ordered=True, ns="ls-")


@savepoints
def test_store_outbox(savepoints):
    assert_outbox_contract(DjangoStore(savepoints=savepoints), ns="ob-")


@savepoints
def test_store_purge(savepoints):
    assert_purge_contract(DjangoStore(savepoints=savepoints), ns="pg-")


@savepoints
def test_store_ids_with_prefix(savepoints):
    assert_ids_with_prefix_contract(DjangoStore(savepoints=savepoints), ns="ip_%-")


@savepoints
def test_store_definitions(savepoints):
    assert_definitions_contract(DjangoStore(savepoints=savepoints), ns="def-")


@savepoints
def test_transport(savepoints):
    def make(clock):
        _empty_tables()
        return DjangoTransport(clock=clock, savepoints=savepoints)

    assert_transport_contract(make)


@savepoints
def test_transport_routes_by_machine(savepoints):
    def make(clock):
        _empty_tables()
        return DjangoTransport(clock=clock, savepoints=savepoints)

    assert_machine_routing_contract(make)
