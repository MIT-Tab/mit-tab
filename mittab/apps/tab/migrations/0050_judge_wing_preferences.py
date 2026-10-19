from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tab", "0049_merge_20261015_1642"),
    ]

    operations = [
        migrations.AddField(
            model_name="judge",
            name="request_wing",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Prioritize giving this judge a wing when they chair an inround."
                ),
            ),
        ),
        migrations.AddField(
            model_name="judge",
            name="no_wings",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Deprioritize giving this judge a wing when they chair an inround."
                ),
            ),
        ),
    ]
