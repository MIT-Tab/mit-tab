"""Reinterpret legacy UTC clock values as intended Eastern local times."""

from datetime import timezone
from zoneinfo import ZoneInfo

from django.db import migrations


SCHEDULE_FIELDS = (
    "new_registrations_open_at",
    "new_registrations_close_at",
    "registration_edits_open_at",
    "registration_edits_close_at",
)
EASTERN = ZoneInfo("America/New_York")


def reinterpret_schedules(apps, schema_editor, *, reverse=False):
    config_model = apps.get_model("registration", "RegistrationConfig")
    configs = config_model.objects.using(schema_editor.connection.alias)
    for config in configs.iterator():
        values = {}
        for field in SCHEDULE_FIELDS:
            value = getattr(config, field)
            if value is None:
                continue
            if reverse:
                value = value.astimezone(EASTERN).replace(tzinfo=timezone.utc)
            else:
                # fold=0 picks the first occurrence in fall and the pre-transition
                # offset for a nonexistent spring time (rolling it forward).
                value = value.astimezone(timezone.utc).replace(tzinfo=EASTERN, fold=0)
            values[field] = value.astimezone(timezone.utc)
        if values:
            configs.filter(pk=config.pk).update(**values)


def restore_utc_clock_values(apps, schema_editor):
    reinterpret_schedules(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [
        ("registration", "0007_registration_dietary_requests_and_more"),
    ]

    operations = [
        migrations.RunPython(reinterpret_schedules, restore_utc_clock_values),
    ]
