from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tab", "0032_manualjudgeassignment"),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicDisplaySetting",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.CharField(max_length=50, unique=True)),
                ("label", models.CharField(max_length=100)),
                ("display_type", models.CharField(choices=[("ranking", "Ranking"), ("standing", "Standing"), ("ballot", "Ballot")], max_length=20)),
                ("is_enabled", models.BooleanField(default=False)),
                ("include_speaks", models.BooleanField(default=False)),
                ("include_ranks", models.BooleanField(default=False)),
                ("max_visible", models.PositiveIntegerField(default=10)),
                ("round_number", models.PositiveIntegerField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "public display setting",
            },
        ),
    ]
