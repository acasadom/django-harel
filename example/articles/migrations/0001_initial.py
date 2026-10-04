from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Article",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("body", models.TextField()),
                ("author", models.CharField(max_length=255)),
                ("status", models.CharField(default="draft", max_length=32)),
                ("reviewer_note", models.TextField(blank=True, default="")),
                ("published_at", models.DateTimeField(blank=True, null=True)),
            ],
        ),
    ]
