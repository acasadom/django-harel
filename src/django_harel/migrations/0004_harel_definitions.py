"""`harel_definitions` — published machine versions (harel's `SCHEMA_VERSION` 3 -> 4):
`ExecutionStore.publish_definition`/`load_definition`, which `DjangoStore` now implements.
Part of harel's own schema, like the tables `0001_initial` creates."""

from django.db import migrations

from django_harel import schema


def create_definitions(apps, schema_editor):
    schema_editor.execute(schema.definitions_create_statement(schema_editor.connection.vendor), params=None)


def drop_definitions(apps, schema_editor):
    schema_editor.execute(schema.definitions_drop_statement(), params=None)


class Migration(migrations.Migration):
    dependencies = [("django_harel", "0003_harelmachine")]

    operations = [
        # MySQL can't roll DDL back, and Django refuses DDL inside a transaction there
        migrations.RunPython(create_definitions, drop_definitions, atomic=False),
    ]
