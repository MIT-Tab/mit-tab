from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("registration", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="RegistrationContent",
        ),
    ]
