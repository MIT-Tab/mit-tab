# Generated manually to enforce unique check-ins per round.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tab", "0028_team_ranking_public"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="checkin",
            constraint=models.UniqueConstraint(
                fields=("judge", "round_number"),
                name="unique_judge_checkin_per_round",
            ),
        ),
        migrations.AddConstraint(
            model_name="roomcheckin",
            constraint=models.UniqueConstraint(
                fields=("room", "round_number"),
                name="unique_room_checkin_per_round",
            ),
        ),
    ]
