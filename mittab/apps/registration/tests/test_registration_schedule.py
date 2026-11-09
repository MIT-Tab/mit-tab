"""Eastern schedule entry, display, enforcement, and legacy data migration."""

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from django.utils import timezone

from mittab.apps.registration.forms import RegistrationSettingsForm
from mittab.apps.registration.models import RegistrationConfig
from mittab.apps.registration.tests.test_registration_admin import (  # pylint: disable=unused-import
    fixture_logged_in_admin_client,
)


SCHEDULE_FIELDS = (
    "new_registrations_open_at",
    "new_registrations_close_at",
    "registration_edits_open_at",
    "registration_edits_close_at",
)


@pytest.mark.django_db
@pytest.mark.parametrize("month, offset", [(1, 5), (7, 4)])
@pytest.mark.parametrize("active_zone", ["UTC", "America/Los_Angeles"])
def test_schedule_saves_and_reloads_eastern_time(
        logged_in_admin_client, month, offset, active_zone):
    wall_time = datetime(2026, month, 15, 17, tzinfo=dt_timezone.utc)
    values = {
        name: (wall_time + timedelta(days=index)).strftime("%Y-%m-%dT%H:%M")
        for index, name in enumerate(SCHEDULE_FIELDS)
    }
    with timezone.override(active_zone):
        response = logged_in_admin_client.post(reverse("registration_admin"), {
            **values,
            "allow_new_registrations": "on",
            "allow_registration_edits": "on",
        })
        assert response.status_code == 302
        config = RegistrationConfig.get_active()
        for index, name in enumerate(SCHEDULE_FIELDS):
            expected = wall_time + timedelta(days=index, hours=offset)
            assert getattr(config, name) == expected

        response = logged_in_admin_client.get(reverse("registration_admin"))
        assert response.status_code == 200
        assert b"Eastern time (EST/EDT)" in response.content
        for name, value in values.items():
            displayed = response.context["form"][name].value()
            assert displayed.strftime("%Y-%m-%dT%H:%M") == value

    for can_register, opening, closing in (
        (config.can_create, config.new_registrations_open_at,
         config.new_registrations_close_at),
        (config.can_modify, config.registration_edits_open_at,
         config.registration_edits_close_at),
    ):
        assert not can_register(opening - timedelta(microseconds=1))
        assert can_register(opening)
        assert can_register(closing - timedelta(microseconds=1))
        assert not can_register(closing)


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["2026-03-08T02:30", "2026-11-01T01:30"])
def test_schedule_rejects_nonexistent_or_ambiguous_eastern_time(value):
    form = RegistrationSettingsForm(data={"new_registrations_open_at": value})
    assert not form.is_valid()
    assert "new_registrations_open_at" in form.errors


@pytest.mark.django_db
@pytest.mark.parametrize("month, offset, abbreviation", [(1, 5, "EST"), (7, 4, "EDT")])
def test_django_admin_uses_eastern_schedules(
        logged_in_admin_client, month, offset, abbreviation):
    config = RegistrationConfig.get_or_create_active()
    value = f"2026-{month:02d}-15T17:00"
    url = reverse("admin:registration_registrationconfig_change", args=[config.pk])
    response = logged_in_admin_client.post(url, {
        "allow_new_registrations": "on",
        "allow_registration_edits": "on",
        "disc_scratch_quantity": "0",
        **dict.fromkeys(SCHEDULE_FIELDS, value),
    })
    assert response.status_code == 302
    config.refresh_from_db()
    for name in SCHEDULE_FIELDS:
        assert getattr(config, name) == datetime(
            2026, month, 15, 17 + offset, tzinfo=dt_timezone.utc,
        )
    response = logged_in_admin_client.get(url)
    assert response.status_code == 200
    assert response.content.count(f'value="{value}"'.encode()) == 4
    response = logged_in_admin_client.get(
        reverse("admin:registration_registrationconfig_changelist"),
    )
    assert response.status_code == 200
    assert f"2026-{month:02d}-15 17:00 {abbreviation}".encode() in response.content


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("month, offset", [(1, 5), (7, 4)])
@pytest.mark.parametrize("empty", [False, True])
def test_schedule_migration_preserves_clock_times_and_other_data(month, offset, empty):
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old_target = [("registration", "0007_registration_dietary_requests_and_more")]
    executor.migrate(old_target)
    try:
        old_apps = executor.loader.project_state(old_target).apps
        config_model = old_apps.get_model("registration", "RegistrationConfig")
        wall_time = datetime(
            2026, month, 15, 23, 30, 12, 123456, tzinfo=dt_timezone.utc,
        )
        old_values = {
            name: None if empty else wall_time + timedelta(days=index)
            for index, name in enumerate(SCHEDULE_FIELDS)
        }
        config = config_model.objects.create(
            **old_values, allow_new_registrations=False, crash_housing_enabled=True,
        )
        updated_at = config.updated_at
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        config.refresh_from_db()
        for name, value in old_values.items():
            expected = value + timedelta(hours=offset) if value else None
            assert getattr(config, name) == expected
        assert config.updated_at == updated_at
        assert not config.allow_new_registrations
        assert config.crash_housing_enabled

        MigrationExecutor(connection).migrate(old_target)
        config.refresh_from_db()
        for name, value in old_values.items():
            assert getattr(config, name) == value
    finally:
        MigrationExecutor(connection).migrate(latest)
