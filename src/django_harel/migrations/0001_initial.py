from django.db import migrations, models

from django_harel import schema


def create_schema(apps, schema_editor):
    for statement in schema.create_statements(schema_editor.connection.vendor):
        schema_editor.execute(statement, params=None)


def drop_schema(apps, schema_editor):
    for statement in schema.drop_statements(schema_editor.connection.vendor):
        schema_editor.execute(statement, params=None)


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        # MySQL can't roll DDL back, and Django refuses DDL inside a transaction there
        migrations.RunPython(create_schema, drop_schema, atomic=False),
        migrations.CreateModel(
            name="HarelExecution",
            fields=[
                ("id", models.CharField(max_length=255, primary_key=True, serialize=False)),
                ("definition_id", models.CharField(max_length=255)),
                ("data", models.TextField()),
                ("version", models.IntegerField()),
            ],
            options={
                "verbose_name": "execution",
                "db_table": "harel_executions",
                "managed": False,
            },
        ),
    ]
