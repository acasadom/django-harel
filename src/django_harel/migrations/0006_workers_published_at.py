"""harel's `SCHEMA_VERSION` 5 -> 6: `harel_workers`, the workers' heartbeats
(`ExecutionStore.record_worker` / `list_workers` / `forget_worker`, which `DjangoStore` now
implements and `manage.py harel_worker` records), and `harel_transport_messages.published_at`,
when each queued message was published (`Transport.backlog`, which `DjangoTransport` now
implements). Part of harel's own schema, like the tables `0001_initial` creates."""

from django.db import migrations

from django_harel import schema


def _has_published_at(schema_editor) -> bool:
    """A database migrated from scratch gets it already: `0001_initial` reads today's frozen
    schema — this migration is then only for a database that migrated past 0005 before harel
    added it."""
    with schema_editor.connection.cursor() as cursor:
        columns = schema_editor.connection.introspection.get_table_description(cursor, schema.MESSAGES_TABLE)
    return any(c.name == "published_at" for c in columns)


def upgrade(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    schema_editor.execute(schema.workers_create_statement(vendor), params=None)
    if not _has_published_at(schema_editor):
        schema_editor.execute(schema.published_at_statement(vendor), params=None)


def downgrade(apps, schema_editor):
    if _has_published_at(schema_editor):
        schema_editor.execute(schema.published_at_drop_statement(), params=None)
    schema_editor.execute(schema.workers_drop_statement(), params=None)


class Migration(migrations.Migration):
    dependencies = [("django_harel", "0005_routing_key")]

    operations = [
        # MySQL can't roll DDL back, and Django refuses DDL inside a transaction there
        migrations.RunPython(upgrade, downgrade, atomic=False),
    ]
