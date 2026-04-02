from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tab", "0036_alter_outround_room_nullable"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="no_protection",
            field=models.BooleanField(default=False),
        ),
    ]
