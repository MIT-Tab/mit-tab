from django.db import migrations, models


def create_default_ga(apps, schema_editor):
    del schema_editor
    info_text = apps.get_model("registration", "InfoText")
    info_text.objects.get_or_create(
        title="GA",
        defaults={"text": "TBD"},
    )


def remove_default_ga(apps, schema_editor):
    del schema_editor
    info_text = apps.get_model("registration", "InfoText")
    info_text.objects.filter(title="GA", text="TBD").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0005_email_content_tracking"),
    ]

    operations = [
        migrations.CreateModel(
            name="InfoText",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=80)),
                ("text", models.CharField(max_length=500)),
                ("is_active", models.BooleanField(default=True)),
                ("display_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["display_order", "created_at"],
            },
        ),
        migrations.RunPython(create_default_ga, remove_default_ga),
    ]
