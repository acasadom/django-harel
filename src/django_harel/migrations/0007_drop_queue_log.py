"""Drop `django_harel_queue_log`: harel's own `harel_transport_messages.published_at` (added by
`0006_workers_published_at`) records when each queued message was published now. The log's
times move to the messages first, for those queued before 0006."""

from django.db import migrations

from django_harel import schema


def drop_queue_log(apps, schema_editor):
    messages, log = schema.MESSAGES_TABLE, schema.QUEUE_LOG
    schema_editor.execute(
        f"UPDATE {messages} SET published_at = (SELECT q.published_at FROM {log} q "
        f"WHERE q.seq = {messages}.seq) WHERE published_at IS NULL",
        params=None,
    )
    schema_editor.execute(schema.queue_log_drop_statement(), params=None)


def create_queue_log(apps, schema_editor):
    schema_editor.execute(schema.queue_log_create_statement(schema_editor.connection.vendor), params=None)
    schema_editor.execute(
        f"INSERT INTO {schema.QUEUE_LOG} (seq, published_at) SELECT seq, published_at "
        f"FROM {schema.MESSAGES_TABLE} WHERE published_at IS NOT NULL",
        params=None,
    )


class Migration(migrations.Migration):
    dependencies = [("django_harel", "0006_workers_published_at")]

    operations = [
        # MySQL can't roll DDL back, and Django refuses DDL inside a transaction there
        migrations.RunPython(drop_queue_log, create_queue_log, atomic=False),
    ]
