from django.db import migrations, models


def create_default_home_shortcuts(apps, schema_editor):
    PublicHomeShortcut = apps.get_model("tab", "PublicHomeShortcut")
    defaults = (
        "released_pairings",
        "missing_ballots",
        "submit_e_ballot",
        "judge_list",
        "team_list",
        "varsity_outrounds",
        "novice_outrounds",
    )

    for index, nav_item in enumerate(defaults, start=1):
        PublicHomeShortcut.objects.update_or_create(
            position=index,
            defaults={"nav_item": nav_item},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tab", "0033_publicdisplaysetting"),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicHomeShortcut",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveSmallIntegerField(choices=[(1, "Slot 1"), (2, "Slot 2"), (3, "Slot 3"), (4, "Slot 4"), (5, "Slot 5"), (6, "Slot 6"), (7, "Slot 7")], unique=True)),
                ("nav_item", models.CharField(choices=[("released_pairings", "Released Pairings"), ("missing_ballots", "Missing Ballots"), ("submit_e_ballot", "Submit E-Ballot"), ("judge_list", "Judge List"), ("team_list", "Team List"), ("varsity_outrounds", "Varsity Outrounds"), ("novice_outrounds", "Novice Outrounds"), ("public_team_results", "Public Team Results"), ("public_speaker_results", "Public Speaker Results"), ("public_ballots", "Public Ballots")], max_length=50)),
            ],
            options={
                "verbose_name": "public home shortcut",
                "ordering": ["position"],
            },
        ),
        migrations.RunPython(create_default_home_shortcuts, migrations.RunPython.noop),
    ]
