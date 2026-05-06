from django.db import migrations


def update_judge_portal_shortcut(apps, schema_editor):
    PublicHomePage = apps.get_model("tab", "PublicHomePage")
    PublicHomePage.objects.filter(slug="submit_e_ballot").update(
        title="Judge Portal and Ballots",
        subtitle="Judges enter codes to update availability and file results.",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("tab", "0042_merge_20260503_0000"),
        ("tab", "0044_usertournamentsetuppreference"),
    ]

    operations = [
        migrations.RunPython(
            update_judge_portal_shortcut,
            migrations.RunPython.noop,
        ),
    ]
