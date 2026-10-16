"""`harel_transport_groups.routing_key` (harel's `SCHEMA_VERSION` 4 -> 5): the machine a group's
tree started from — what `ExecutionStore.claim`'s new `routing_keys` filters on, which
`DjangoTransport` now implements, and `manage.py harel_worker --machines` uses."""

from django.db import migrations

from django_harel import schema

_GROUPS = f"{schema.PREFIX}_transport_groups"


def _has_routing_key(schema_editor) -> bool:
    """A database migrated from scratch gets it already: `0001_initial` reads today's frozen
    schema, which already has `routing_key` — this migration is then only for a database that
    migrated past 0004 before harel added it."""
    with schema_editor.connection.cursor() as cursor:
        columns = schema_editor.connection.introspection.get_table_description(cursor, _GROUPS)
    return any(c.name == "routing_key" for c in columns)


def add_routing_key(apps, schema_editor):
    if _has_routing_key(schema_editor):
        return
    for statement in schema.routing_key_statements(schema_editor.connection.vendor):
        schema_editor.execute(statement, params=None)


def drop_routing_key(apps, schema_editor):
    if not _has_routing_key(schema_editor):
        return
    for statement in schema.routing_key_drop_statements(schema_editor.connection.vendor):
        schema_editor.execute(statement, params=None)


class Migration(migrations.Migration):
    dependencies = [("django_harel", "0004_harel_definitions")]

    operations = [
        # MySQL can't roll DDL back, and Django refuses DDL inside a transaction there
        migrations.RunPython(add_routing_key, drop_routing_key, atomic=False),
    ]
