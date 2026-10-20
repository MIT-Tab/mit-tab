from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tab", "0050_judge_wing_preferences"),
    ]

    operations = [
        migrations.AddField(
            model_name="round",
            name="result_entered_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
