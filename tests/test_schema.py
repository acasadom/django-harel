"""The migration creates harel's own schema."""

import importlib

import pytest
from django.db import connection
from harel.engine import schema as harel_schema
from harel.engine.execution import Execution
from harel.engine.store.base import DefinitionRecord, WorkerRecord
from harel.spec.states import Event

from django_harel.schema import TABLES
from django_harel.store import DjangoStore
from django_harel.transport import DjangoTransport

frozen = importlib.import_module("django_harel.migrations.0001_initial")


def test_the_frozen_schema_is_harels():
    """When harel changes its schema (SCHEMA_VERSION), django-harel needs a migration to it."""
    assert harel_schema.SCHEMA_VERSION == frozen.SCHEMA_VERSION, (
        "harel's schema moved on: add a migration for it"
    )
    assert list(frozen.SQLITE_STORE) == harel_schema.store_schema("sqlite")
    assert list(frozen.SQLITE_TRANSPORT) == harel_schema.transport_schema("sqlite")
    assert list(frozen.POSTGRES_STORE) == harel_schema.store_schema("postgres")
    assert list(frozen.POSTGRES_TRANSPORT) == harel_schema.transport_schema("postgres")
    assert frozen.TABLES == TABLES


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

        # DjangoTransport's own SQL and harel's `harel_claim` function agree on routing_key too
        DjangoTransport().publish("routed", Event(kind="Go"), routing_key="order")
        assert transport.claim("native", 30, routing_keys=frozenset({"fulfilment"})) is None
        lease = transport.claim("native", 30, routing_keys=frozenset({"order"}))
        assert lease.group_id == "routed"
        transport.ack(lease)

        # a version Django published, read by harel's own backend, and the other way round
        record = DefinitionRecord("job", 1, "abcd", {"main": "job.stm", "files": {}}, published_at=1.0)
        DjangoStore().publish_definition(record)
        assert store.load_definition("job", 1) == record
        native = DefinitionRecord("order", 1, "efgh", {"main": "order.stm", "files": {}})
        store.publish_definition(native)
        assert DjangoStore().load_definition("order", 1) == native

        # a heartbeat, and the backlog's publish times, either way too
        DjangoStore().record_worker(WorkerRecord("django", ("order",), 1.0, 2.0, 10.0))
        store.record_worker(WorkerRecord("native", None, 3.0, 4.0, 10.0))
        assert [w.worker_id for w in store.list_workers()] == ["django", "native"]
        assert DjangoStore().list_workers() == store.list_workers()
        DjangoTransport(clock=lambda: 7.0).publish("aging", Event(kind="Go"), routing_key="order")
        assert [(b.routing_key, b.oldest_published_at) for b in transport.backlog().machines] == [
            ("order", 7.0)
        ]
        assert DjangoTransport().backlog() == transport.backlog()
    finally:
        store.close()
        transport.close()
