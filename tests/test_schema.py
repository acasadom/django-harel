"""The migration creates harel's own schema."""

import pytest
from django.db import connection
from harel.engine import schema as harel_schema
from harel.engine.execution import Execution
from harel.spec.states import Event

from django_harel import _frozen_schema as frozen
from django_harel.store import DjangoStore
from django_harel.transport import DjangoTransport


def test_the_frozen_schema_is_harels():
    """When harel changes its schema (SCHEMA_VERSION), django-harel needs a migration to it."""
    assert harel_schema.SCHEMA_VERSION == frozen.SCHEMA_VERSION, (
        "harel's schema moved on: add a migration for it"
    )
    assert list(frozen.SQLITE_STORE) == harel_schema.store_schema("sqlite")
    assert list(frozen.SQLITE_TRANSPORT) == harel_schema.transport_schema("sqlite")
    assert list(frozen.POSTGRES_STORE) == harel_schema.store_schema("postgres")
    assert list(frozen.POSTGRES_TRANSPORT) == harel_schema.transport_schema("postgres")


@pytest.mark.django_db(transaction=True)
def test_harels_postgres_backends_read_what_django_writes():
    if connection.vendor != "postgresql":
        pytest.skip("harel's native backends over the same tables: PostgreSQL")
    import psycopg
    from harel.engine.store import PostgresStore
    from harel.engine.transport import PostgresTransport

    exe = Execution(id="shared", definition_id="job", context={"n": 1})
    DjangoStore().commit(exe, [("shared", Event(kind="Go"))], processed_event_id="e1")
    DjangoTransport().publish("shared", Event(kind="Go"), priority=2)

    params = connection.get_connection_params()
    conninfo = psycopg.conninfo.make_conninfo(
        host=params["host"], port=params["port"], user=params["user"],
        password=params["password"], dbname=params["dbname"],
    )  # fmt: skip
    store = PostgresStore(psycopg.connect(conninfo), create_schema=False)
    transport = PostgresTransport(psycopg.connect(conninfo), create_schema=False)
    try:
        loaded = store.load("shared")
        assert loaded.context == {"n": 1} and loaded.version == 1
        assert store.is_processed("shared", "e1")
        assert [e.event.kind for e in store.pending_outbox()] == ["Go"]

        assert transport.claim("native", 30, min_priority=3) is None
        lease = transport.claim("native", 30, min_priority=2)
        assert lease.group_id == "shared" and lease.event.kind == "Go"
        transport.ack(lease)
        assert transport.claim("native", 30) is None
    finally:
        store.close()
        transport.close()
