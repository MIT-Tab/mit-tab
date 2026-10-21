from django.db import migrations


def add_tab_cards_page(apps, schema_editor):
    apps.get_model("tab", "PublicHomePage").objects.using(
        schema_editor.connection.alias
    ).get_or_create(
        slug="public_tab_cards",
        defaults={
            "title": "Tab Cards",
            "subtitle": "Team results, speaker scores, and round records.",
            "url_path": "/public/tab-cards/",
            "sort_order": 13,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("tab", "0052_round_result_entered_at")]
    operations = [migrations.RunPython(add_tab_cards_page, migrations.RunPython.noop)]
