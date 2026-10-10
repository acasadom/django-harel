"""`django_harel_queue_log` — when each queued message was published, for the admin dashboard
(see `django_harel.schema.QUEUE_LOG`). Not part of harel's own schema."""

from django.db import migrations

from django_harel import schema


def create_queue_log(apps, schema_editor):
    schema_editor.execute(schema.queue_log_create_statement(schema_editor.connection.vendor), params=None)


def drop_queue_log(apps, schema_editor):
    schema_editor.execute(schema.queue_log_drop_statement(), params=None)


class Migration(migrations.Migration):
    dependencies = [("django_harel", "0001_initial")]

    operations = [
        # MySQL can't roll DDL back, and Django refuses DDL inside a transaction there
        migrations.RunPython(create_queue_log, drop_queue_log, atomic=False),
    ]
