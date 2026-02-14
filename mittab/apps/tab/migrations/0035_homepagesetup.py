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


def create_default_home_pages(apps, schema_editor):
    PublicHomePage = apps.get_model("tab", "PublicHomePage")
    defaults = (
        {
            "slug": "released_pairings",
            "title": "Released Pairings",
            "subtitle": "Latest rounds and room assignments.",
            "url_path": "/public/pairings/",
        },
        {
            "slug": "missing_ballots",
            "title": "Missing Ballots",
            "subtitle": "Outstanding ballots that need attention.",
            "url_path": "/public/missing-ballots/",
        },
        {
            "slug": "submit_e_ballot",
            "title": "Submit E-Ballot",
            "subtitle": "Judges enter ballot codes to file results.",
            "url_path": "/public/e-ballots/",
        },
        {
            "slug": "judge_list",
            "title": "Judge List",
            "subtitle": "Roster and check-in status.",
            "url_path": "/public/judges/",
        },
        {
            "slug": "team_list",
            "title": "Team List",
            "subtitle": "Registered teams and schools.",
            "url_path": "/public/teams/",
        },
        {
            "slug": "varsity_outrounds",
            "title": "Varsity Outrounds",
            "subtitle": "Varsity elimination brackets and pairings.",
            "url_path": "/public/outrounds/0/",
        },
        {
            "slug": "novice_outrounds",
            "title": "Novice Outrounds",
            "subtitle": "Novice elimination brackets and pairings.",
            "url_path": "/public/outrounds/1/",
        },
        {
            "slug": "public_team_results",
            "title": "Public Team Results",
            "subtitle": "Standings and published team records.",
            "url_path": "/public/team-rankings/",
        },
        {
            "slug": "public_speaker_results",
            "title": "Public Speaker Results",
            "subtitle": "Published speaker rankings and records.",
            "url_path": "/public/speaker-rankings/",
        },
        {
            "slug": "public_ballots",
            "title": "Public Ballots",
            "subtitle": "Published ballots and detailed round results.",
            "url_path": "/public/ballots/",
        },
    )

    for index, definition in enumerate(defaults, start=1):
        PublicHomePage.objects.get_or_create(
            slug=definition["slug"],
            defaults={
                "title": definition["title"],
                "subtitle": definition["subtitle"],
                "url_path": definition["url_path"],
                "sort_order": index,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tab", "0034_add_motion_model"),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicHomeShortcut",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveSmallIntegerField(choices=[(1, "Slot 1"), (2, "Slot 2"), (3, "Slot 3"), (4, "Slot 4"), (5, "Slot 5"), (6, "Slot 6"), (7, "Slot 7")], unique=True)),
                ("nav_item", models.CharField(db_index=True, max_length=50)),
            ],
            options={
                "verbose_name": "public home shortcut",
                "ordering": ["position"],
            },
        ),
        migrations.CreateModel(
            name="PublicHomePage",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.CharField(max_length=50, unique=True)),
                ("title", models.CharField(max_length=100)),
                ("subtitle", models.CharField(blank=True, default="", max_length=255)),
                ("url_path", models.CharField(max_length=200)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "public home page",
                "ordering": ["sort_order", "title"],
            },
        ),
        migrations.RunPython(create_default_home_shortcuts, migrations.RunPython.noop),
        migrations.RunPython(create_default_home_pages, migrations.RunPython.noop),
    ]
