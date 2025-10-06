from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tab", "0025_alter_tabsettings_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="debater",
            name="qualified",
            field=models.BooleanField(default=False),
        ),
    ]
