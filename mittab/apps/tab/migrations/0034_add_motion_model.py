from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tab", "0033_publicdisplaysetting"),
    ]

    operations = [
        migrations.CreateModel(
            name="Motion",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("round_number", models.IntegerField(blank=True, help_text="Round number for inrounds (1, 2, 3, etc.)", null=True)),
                ("outround_type", models.IntegerField(blank=True, choices=[(0, "Varsity"), (1, "Novice")], help_text="Type of outround (Varsity/Novice)", null=True)),
                ("num_teams", models.IntegerField(blank=True, help_text="Number of teams in outround (e.g., 8 for quarterfinals)", null=True)),
                ("info_slide", models.TextField(blank=True, default="", help_text="Optional context/info slide text shown before the motion")),
                ("motion_text", models.TextField(help_text="The debate motion/resolution text")),
                ("is_published", models.BooleanField(default=False, help_text="Whether this motion is visible to the public")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "motion",
                "verbose_name_plural": "motions",
                "ordering": ["round_number", "outround_type", "-num_teams"],
            },
        ),
    ]
