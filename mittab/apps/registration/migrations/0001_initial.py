from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tab", "0025_alter_tabsettings_key"),
    ]

    operations = [
        migrations.CreateModel(
            name="RegistrationConfig",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("registration_open", models.DateField(blank=True, null=True)),
                ("registration_close", models.DateField(blank=True, null=True)),
                ("tournament_start", models.DateField(blank=True, null=True)),
                ("extra_information", models.TextField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="Registration",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("herokunator_code", models.CharField(blank=True, max_length=255, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("school", models.ForeignKey(on_delete=models.deletion.CASCADE, to="tab.school")),
            ],
        ),
        migrations.CreateModel(
            name="RegistrationTeam",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_free_seed", models.BooleanField(default=False)),
                ("registration", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="teams", to="registration.registration")),
                ("team", models.ForeignKey(on_delete=models.deletion.CASCADE, to="tab.team")),
            ],
        ),
        migrations.CreateModel(
            name="RegistrationTeamMember",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.IntegerField(default=0)),
                ("debater", models.ForeignKey(on_delete=models.deletion.CASCADE, to="tab.debater")),
                ("registration_team", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="members", to="registration.registrationteam")),
                ("school", models.ForeignKey(on_delete=models.deletion.CASCADE, to="tab.school")),
            ],
        ),
        migrations.CreateModel(
            name="RegistrationJudge",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("judge", models.ForeignKey(on_delete=models.deletion.CASCADE, to="tab.judge")),
                ("registration", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="judges", to="registration.registration")),
            ],
        ),
    ]
