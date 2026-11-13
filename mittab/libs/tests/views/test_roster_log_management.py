"""Publication timing, import exclusion, and editable public-only history."""

# pylint: disable=redefined-outer-name,unused-argument
from datetime import datetime, timezone as datetime_timezone

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse

from mittab.apps.registration.models import RegistrationConfig
from mittab.apps.tab.auth_roles import APDA_BOARD_GROUP_NAME
from mittab.apps.tab.models import (
    AuditEvent,
    Judge,
    PublicRosterChange,
    PublicRosterLogState,
    Round,
    School,
    TabSettings,
    Team,
)
from mittab.apps.tab.public_roster_log import (
    logging_enabled,
    suppress_public_roster_logging,
)
from mittab.libs.data_import.import_judges import JudgeImporter
from mittab.libs.data_import.import_teams import TeamImporter
from mittab.libs.tests.data_import import MockWorkbook

pytestmark = pytest.mark.django_db


@pytest.fixture
def unpublished_roster(db):
    config = RegistrationConfig.get_or_create_active()
    config.allow_new_registrations = True
    config.save()
    TabSettings.set("teams_public", 0)
    TabSettings.set("judges_public", 1)
    TabSettings.set("start_tournament_wizard_completed", 1)
    judge = Judge.objects.create(name="Initial judge", rank=4)
    return config, judge


def rename(judge, name):
    judge.name = name
    judge.save()


def test_publication_during_open_registration_waits_for_close(unpublished_roster):
    config, judge = unpublished_roster
    TabSettings.set("teams_public", 1)
    state = PublicRosterLogState.objects.get(pk=1)
    assert state.first_published_at is not None
    assert state.started_at is None
    rename(judge, "While registration open")
    assert not PublicRosterChange.objects.exists()
    config.allow_new_registrations = False
    config.save()
    state.refresh_from_db()
    assert state.started_at >= state.first_published_at
    rename(judge, "After close")
    assert PublicRosterChange.objects.count() == 1


def test_closed_registration_waits_for_first_publication(unpublished_roster):
    config, judge = unpublished_roster
    config.allow_new_registrations = False
    config.save()
    rename(judge, "After close but still private")
    assert not PublicRosterChange.objects.exists()
    assert not PublicRosterLogState.objects.exists()
    TabSettings.set("teams_public", 1)
    rename(judge, "After publication")
    assert PublicRosterChange.objects.count() == 1
    assert "After publication" in PublicRosterChange.objects.get().message


@pytest.mark.parametrize("config_exists", [True, False])
def test_unused_registration_starts_at_publication(unpublished_roster, config_exists):
    config, judge = unpublished_roster
    if config_exists:
        config.allow_new_registrations = False
        config.save()
    else:
        config.delete()
    rename(judge, "Before publication")
    assert not logging_enabled()
    assert not PublicRosterChange.objects.exists()
    TabSettings.set("teams_public", 1)
    assert logging_enabled()
    rename(judge, "First public change")
    first_publication = PublicRosterLogState.objects.get(pk=1).first_published_at
    TabSettings.set("teams_public", 0)
    rename(judge, "Changed after hiding")
    TabSettings.set("teams_public", 1)
    assert (
        PublicRosterLogState.objects.get(pk=1).first_published_at == first_publication
    )
    assert PublicRosterChange.objects.count() == 2


def test_imports_skip_public_history_and_preserve_private_audits(unpublished_roster):
    config, judge = unpublished_roster
    config.allow_new_registrations = False
    config.save()
    TabSettings.set("teams_public", 1)
    TabSettings.set("tot_rounds", 2)
    actor = get_user_model().objects.create_superuser("importer")
    team_importer = TeamImporter(
        MockWorkbook(
            [
                [
                    "Imported team",
                    "Import school",
                    "",
                    "",
                    "Imported debater one",
                    "",
                    "one@example.com",
                    "101",
                    "Imported debater two",
                    "n",
                    "two@example.com",
                    "102",
                ]
            ]
        ),
        created_by=actor,
    )
    assert not team_importer.import_data()
    judge_importer = JudgeImporter(
        MockWorkbook(
            [
                [
                    "Imported judge",
                    "judge@example.com",
                    "4",
                    "Import school",
                    "yes",
                    "yes",
                ]
            ]
        ),
        created_by=actor,
    )
    assert not judge_importer.import_data()
    assert not PublicRosterChange.objects.exists()
    assert AuditEvent.objects.exists()
    assert Team.objects.filter(name="Imported team").exists()
    assert Judge.objects.get(name="Imported judge").expected_checkins.count() == 2
    # An unsuccessful import and nested suppression must not affect later edits.
    assert judge_importer.import_data()
    with pytest.raises(ValueError), suppress_public_roster_logging():
        with suppress_public_roster_logging():
            raise ValueError("Import failed")
    rename(judge, "Manual edit after imports")
    assert PublicRosterChange.objects.count() == 1


@pytest.fixture
def editable_entry(unpublished_roster):
    config, judge = unpublished_roster
    config.allow_new_registrations = False
    config.save()
    TabSettings.set("teams_public", 1)
    entry = PublicRosterChange.objects.create(
        list_kind="teams", message="Original entry"
    )
    admin = get_user_model().objects.create_superuser("log_admin", password="password")
    AuditEvent.record(judge, AuditEvent.EDIT, admin, changes={"private": "untouched"})
    return entry, admin


def test_admin_edits_and_deletes_public_history_without_changing_audits(
    client, editable_entry
):
    entry, admin = editable_entry
    client.force_login(admin)
    audit_before = list(AuditEvent.objects.values())
    assert b"Original entry" in client.get(reverse("public_teams")).content
    manager = client.get(reverse("manage_roster_log"))
    assert manager.status_code == 200
    assert b"Edit or delete" in manager.content
    response = client.post(
        reverse("edit_roster_log", args=[entry.pk]),
        {
            "list_kind": "judges",
            "message": "Corrected entry",
            "occurred_at": "2026-07-02T12:05",
            "action": "save",
            "involves_debaters": "on",
            "changes": "Attempted audit mutation",
        },
    )
    assert response.status_code == 302
    entry.refresh_from_db()
    assert entry.created_at == datetime(2026, 7, 2, 17, 5, tzinfo=datetime_timezone.utc)
    assert entry.list_kind == "judges" and entry.involves_debaters
    assert b"Original entry" not in client.get(reverse("public_teams")).content
    assert b"Corrected entry" in client.get(reverse("public_judges")).content
    assert list(AuditEvent.objects.values()) == audit_before
    response = client.post(
        reverse("edit_roster_log", args=[entry.pk]), {"action": "delete"}
    )
    assert response.status_code == 302
    assert not PublicRosterChange.objects.filter(pk=entry.pk).exists()
    assert b"Corrected entry" not in client.get(reverse("public_judges")).content
    assert list(AuditEvent.objects.values()) == audit_before


def test_editor_rejects_invalid_values_and_requires_csrf(editable_entry):
    entry, admin = editable_entry
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin)
    url = reverse("edit_roster_log", args=[entry.pk])
    assert client.post(url, {"action": "delete"}).status_code == 403
    assert client.get(url).status_code == 200
    response = client.post(
        url,
        {
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
            "list_kind": "private-audits",
            "message": "",
            "occurred_at": "invalid",
            "action": "save",
        },
    )
    assert response.status_code == 200
    assert response.context["form"].errors
    entry.refresh_from_db()
    assert entry.message == "Original entry"


@pytest.mark.parametrize("role", ["anonymous", "staff", "board"])
def test_non_admins_and_board_superusers_cannot_manage_history(
    client, editable_entry, role
):
    entry, admin = editable_entry
    if role == "staff":
        user = get_user_model().objects.create_user("limited_staff", is_staff=True)
        client.force_login(user)
    elif role == "board":
        group, _ = Group.objects.get_or_create(name=APDA_BOARD_GROUP_NAME)
        admin.groups.add(group)
        client.force_login(admin)
        # Open board access, preserving the board restriction over superuser power.
        school = School.objects.create(name="Board school")
        first = Team.objects.create(name="Board one", school=school, seed=0)
        second = Team.objects.create(name="Board two", school=school, seed=0)
        TabSettings.set("tot_rounds", 5)
        Round.objects.create(
            round_number=5, gov_team=first, opp_team=second, victor=Round.GOV
        )
    before = list(PublicRosterChange.objects.values())
    audits = list(AuditEvent.objects.values())
    for url in (
        reverse("manage_roster_log"),
        reverse("edit_roster_log", args=[entry.pk]),
    ):
        assert client.get(url).status_code == 302
        assert client.post(url, {"action": "delete"}).status_code == (
            200 if role == "anonymous" else 302
        )
    assert list(PublicRosterChange.objects.values()) == before
    assert list(AuditEvent.objects.values()) == audits
