from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="HarelExecution",
            fields=[
                ("id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("definition_id", models.CharField(db_index=True, max_length=255)),
                ("data", models.TextField()),
                ("version", models.BigIntegerField(default=0)),
                ("status", models.CharField(db_index=True, default="", max_length=32)),
                ("active_path", models.CharField(blank=True, default="", max_length=255)),
                ("parent_id", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
            ],
            options={"db_table": "harel_executions"},
        ),
        migrations.CreateModel(
            name="HarelOutbox",
            fields=[
                ("seq", models.BigAutoField(primary_key=True, serialize=False)),
                ("target_id", models.CharField(blank=True, max_length=64, null=True)),
                ("event", models.TextField()),
            ],
            options={"db_table": "harel_outbox"},
        ),
        migrations.CreateModel(
            name="HarelProcessedEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("execution_id", models.CharField(max_length=64)),
                ("event_id", models.CharField(max_length=128)),
            ],
            options={"db_table": "harel_processed_events"},
        ),
        migrations.AlterUniqueTogether(
            name="harelprocessedevent",
            unique_together={("execution_id", "event_id")},
        ),
        migrations.CreateModel(
            name="HarelTimer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("execution_id", models.CharField(max_length=64)),
                ("path", models.CharField(max_length=255)),
                ("fire_at", models.FloatField()),
            ],
            options={"db_table": "harel_timers"},
        ),
        migrations.AlterUniqueTogether(
            name="hareltimer",
            unique_together={("execution_id", "path")},
        ),
        migrations.CreateModel(
            name="HarelSpawn",
            fields=[
                ("seq", models.BigAutoField(primary_key=True, serialize=False)),
                ("parent_id", models.CharField(max_length=64)),
                ("child_id", models.CharField(max_length=64)),
                ("root_path", models.CharField(max_length=255)),
                ("context", models.TextField()),
            ],
            options={"db_table": "harel_spawns"},
        ),
        migrations.CreateModel(
            name="HarelTrace",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("execution_id", models.CharField(max_length=64)),
                ("idx", models.IntegerField()),
                ("entry", models.TextField()),
            ],
            options={"db_table": "harel_trace"},
        ),
        migrations.AlterUniqueTogether(
            name="hareltrace",
            unique_together={("execution_id", "idx")},
        ),
        migrations.CreateModel(
            name="HarelQueue",
            fields=[
                ("seq", models.BigAutoField(primary_key=True, serialize=False)),
                ("group_id", models.CharField(db_index=True, max_length=64)),
                ("payload", models.TextField()),
                ("available_at", models.FloatField(db_index=True, default=0.0)),
                ("locked_by", models.CharField(blank=True, max_length=64, null=True)),
                ("lock_expiry", models.FloatField(blank=True, null=True)),
            ],
            options={"db_table": "harel_queue"},
        ),
    ]
