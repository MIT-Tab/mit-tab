"""Delivery, previews, stale content and upgrade coverage for team code emails."""
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from mittab.apps.registration.emails import send_registration_team_portal_emails
from mittab.apps.registration.models import (
    Registration,
    RegistrationConfirmationEmailLog,
)
from mittab.apps.registration.team_emails import (
    prepare_team_code_plan,
    send_team_code_entries,
    team_email_snapshot,
)
from mittab.apps.tab.models import Debater, Judge, JudgeCodeEmailLog, School, Team
from mittab.apps.tab.models import StaffInviteEmailLog, TabSettings, TeamCodeEmailLog
from mittab.apps.tab.staff_invites import send_staff_invite_email
from mittab.apps.tab.views.team_email_views import team_email_management_context
from mittab.libs.cacheing import cache_logic
from mittab.libs.email_service import EmailServiceError

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_email_test_caches():
    cache_logic.clear_cache()
    yield
    cache_logic.clear_cache()


@pytest.fixture(name="team")
def email_team():
    school = School.objects.create(name="Email U")
    obj = Team.objects.create(name="Email U A", school=school, seed=Team.UNSEEDED)
    obj.debaters.add(
        Debater.objects.create(
            name="Alice", email="alice@example.com", novice_status=0),
        Debater.objects.create(name="Bob", email="bob@example.com", novice_status=0),
    )
    return obj


@pytest.fixture(name="email_request")
def team_email_request():
    return RequestFactory().get(reverse("email_management"))


@pytest.fixture(name="staff_client")
def director_client(client):
    user = get_user_model().objects.create_superuser(
        username="email-director", email="director@example.com", password="testpass",
    )
    client.force_login(user)
    return client


def deliver(team, email_request):
    with mock.patch("mittab.apps.registration.team_emails.EmailService") as service:
        service.return_value.send_bulk.return_value = 2
        return send_team_code_entries(prepare_team_code_plan([team], email_request))


def row_for(team, email_request):
    return next(row for row in team_email_management_context(email_request)["rows"]
                if row["team"].pk == team.pk)


def test_registration_sends_and_records_each_team_recipient(team, email_request):
    registration = Registration.objects.create(
        school=team.school, email="school@example.com")
    team.registration = registration
    team.save()
    with mock.patch("mittab.apps.registration.emails.EmailService") as service:
        service.return_value.send_bulk.return_value = 2
        assert send_registration_team_portal_emails(registration, email_request) == 2
        payloads = service.return_value.send_bulk.call_args.args[0]
    assert {item.to_address for item in payloads} == {
        "alice@example.com", "bob@example.com"}
    assert all(team.team_code in item.text_body for item in payloads)
    assert all(team.team_code in item.html_body for item in payloads)
    assert TeamCodeEmailLog.objects.filter(
        source="registration", successful=True).count() == 2
    assert all(log.email_payload["text_body"] for log in TeamCodeEmailLog.objects.all())


def test_repeated_registration_and_manual_sends_share_cooldown(team, email_request):
    registration = Registration.objects.create(
        school=team.school, email="school@example.com")
    team.registration = registration
    team.save()
    deliver(team, email_request)
    with mock.patch("mittab.apps.registration.emails.EmailService") as service:
        assert send_registration_team_portal_emails(registration, email_request) == 0
        service.assert_not_called()
    assert not prepare_team_code_plan([team], email_request)
    TeamCodeEmailLog.objects.update(
        sent_at=timezone.now() - timedelta(hours=6, seconds=1))
    assert len(prepare_team_code_plan([team], email_request)) == 2


def test_manual_team_send_and_preview(staff_client, team):
    response = staff_client.get(reverse("email_management"))
    assert response.status_code == 200
    assert b"Team Codes" in response.content
    assert b"Preview current email" in response.content
    assert team.team_code.encode() in response.content
    with mock.patch("mittab.apps.registration.team_emails.EmailService") as service:
        service.return_value.send_bulk.return_value = 2
        response = staff_client.post(reverse("email_management"), {
            "email_action": "team_codes",
            "team_ids": [team.pk, team.pk, "bogus", "99999"],
        })
    assert response.status_code == 302
    assert TeamCodeEmailLog.objects.filter(team=team, source="manual").count() == 2
    response = staff_client.get(reverse("email_management"))
    assert b"Preview recorded email" in response.content
    assert b'sandbox=""' in response.content


@pytest.mark.parametrize(
    "change", ["team_name", "debater_name", "email", "code", "roster", "tournament"],
)
def test_email_relevant_edits_flag_resend(team, email_request, change):
    deliver(team, email_request)
    original_payload = TeamCodeEmailLog.objects.first().email_payload
    assert row_for(team, email_request)["status"] == "Sent"
    debater = team.debaters.first()
    if change == "team_name":
        team.name = "Updated team"
        team.save()
    elif change == "debater_name":
        debater.name = "Updated speaker"
        debater.save()
    elif change == "email":
        debater.email = "updated@example.com"
        debater.save()
    elif change == "code":
        team.team_code = "updated-code"
        team.save()
    elif change == "roster":
        team.debaters.remove(debater)
    else:
        TabSettings.set("tournament_name", "Updated tournament")
    row = row_for(team, email_request)
    assert row["status"] == "Needs resend"
    assert row["needs_resend"]
    assert TeamCodeEmailLog.objects.first().email_payload == original_payload


def test_irrelevant_team_edit_does_not_flag_resend(team, email_request):
    deliver(team, email_request)
    team.checked_in = not team.checked_in
    team.seed = Team.FULL_SEED
    team.save()
    assert row_for(team, email_request)["status"] == "Sent"


def test_shared_addresses_are_deduplicated_and_rate_limited(team, email_request):
    debaters = list(team.debaters.all())
    debaters[1].email = "ALICE@example.com"
    debaters[1].save()
    assert len(prepare_team_code_plan([team], email_request)) == 1
    other = Team.objects.create(
        name="Email U B", school=team.school, seed=Team.UNSEEDED)
    other.debaters.add(debaters[1])
    assert len(prepare_team_code_plan([team, other], email_request)) == 1
    deliver(team, email_request)
    assert not prepare_team_code_plan([other], email_request)


def test_post_plans_only_selected_teams(staff_client, team):
    other = Team.objects.create(
        name="Email U B", school=team.school, seed=Team.UNSEEDED)
    other.debaters.add(*team.debaters.all())
    with mock.patch("mittab.apps.registration.team_emails.EmailService") as service:
        service.return_value.send_bulk.return_value = 2
        staff_client.post(reverse("email_management"), {
            "email_action": "team_codes", "team_ids": [other.pk],
        })
    assert TeamCodeEmailLog.objects.filter(team=other, successful=True).count() == 2
    assert not TeamCodeEmailLog.objects.filter(team=team).exists()


def test_partial_failure_records_content_and_obeys_cooldown(team, email_request):
    entries = prepare_team_code_plan([team], email_request)
    with mock.patch("mittab.apps.registration.team_emails.EmailService") as service:
        service.return_value.send_bulk.side_effect = EmailServiceError(
            "second send failed", sent_requests=[entries[0]["email_request"]],
        )
        with pytest.raises(EmailServiceError):
            send_team_code_entries(entries)
    assert TeamCodeEmailLog.objects.filter(successful=True).count() == 1
    failed = TeamCodeEmailLog.objects.get(successful=False)
    assert failed.error_message == "second send failed"
    assert failed.email_payload["to_address"] == failed.email
    assert not prepare_team_code_plan([team], email_request)
    TeamCodeEmailLog.objects.update(sent_at=timezone.now() - timedelta(hours=7))
    retry = prepare_team_code_plan([team], email_request)
    assert len(retry) == 2
    assert row_for(team, email_request)["needs_resend"]
    with mock.patch("mittab.apps.registration.team_emails.EmailService") as service:
        service.return_value.send_bulk.return_value = 2
        send_team_code_entries(retry)
    assert row_for(team, email_request)["status"] == "Sent"


def test_configuration_failure_does_not_start_cooldown(team, email_request):
    with mock.patch("mittab.apps.registration.team_emails.EmailService") as service:
        service.side_effect = ImproperlyConfigured("SES unavailable")
        with pytest.raises(ImproperlyConfigured):
            send_team_code_entries(prepare_team_code_plan([team], email_request))
    assert TeamCodeEmailLog.objects.filter(successful=False).count() == 2
    assert len(prepare_team_code_plan([team], email_request)) == 2


def test_legacy_team_is_not_selected_and_edits_are_detected(team, email_request):
    team.code_email_history_unknown = True
    team.code_email_baseline = team_email_snapshot(team, "Tournament")
    team.save()
    row = row_for(team, email_request)
    assert row["status"] == "Earlier delivery unknown"
    assert not row["checked"]
    team.name = "Edited legacy team"
    team.save()
    assert row_for(team, email_request)["needs_resend"]
    deliver(team, email_request)
    assert row_for(team, email_request)["status"] == "Sent"


def test_missing_addresses_disable_send(team, email_request):
    team.debaters.update(email="")
    row = row_for(team, email_request)
    assert row["status"] == "Missing email"
    assert not row["can_send"]


def test_old_history_and_current_preview_during_cooldown(staff_client):
    judge = Judge.objects.create(name="Historical Judge",
                                 email="judge@example.com", rank=5)
    JudgeCodeEmailLog.objects.create(
        judge=judge, email=judge.email, ballot_code=judge.ballot_code)
    response = staff_client.get(reverse("email_management"))
    assert response.status_code == 200
    assert b"Original content unavailable" in response.content
    row = response.context["judge_email"]["rows"][0]
    assert not row["can_send"]
    assert row["preview"].to_address == judge.email


def test_invalid_legacy_codes_do_not_break_email_management(staff_client, team):
    Team.objects.filter(pk=team.pk).update(team_code="invalid/code")
    judge = Judge.objects.create(
        name="Invalid code judge", rank=5, email="judge@example.com",
    )
    Judge.objects.filter(pk=judge.pk).update(ballot_code="invalid/code")
    response = staff_client.get(reverse("email_management"))
    assert response.status_code == 200
    assert response.context["team_email"]["rows"][0]["status"] == "Invalid team code"
    assert not response.context["team_email"]["rows"][0]["can_send"]
    assert response.context["judge_email"]["rows"][0]["preview"] is None


def test_staff_invitation_is_in_history_with_original_preview(staff_client):
    user = get_user_model().objects.get(username="email-director")
    email_request = RequestFactory().get("/")
    with mock.patch("mittab.apps.tab.staff_invites.EmailService") as service:
        service.return_value.send_bulk.return_value = 1
        send_staff_invite_email(email_request, user)
    assert StaffInviteEmailLog.objects.get().email_payload["to_address"] == user.email
    response = staff_client.get(reverse("email_management"))
    assert b"Staff invitation" in response.content
    assert b"Preview recorded email" in response.content


@pytest.mark.django_db(transaction=True)
def test_upgrade_preserves_existing_roster_codes_and_history():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old_targets = [("tab", "0046_judgecodeemaillog_source"),
                   ("registration", "0004_registrationconfirmationemaillog")]
    executor.migrate(old_targets)
    old_apps = executor.loader.project_state(old_targets).apps
    try:
        school = old_apps.get_model("tab", "School").objects.create(name="Existing U")
        registration_model = old_apps.get_model("registration", "Registration")
        registration = registration_model.objects.create(
            school=school, email="existing@example.com",
            herokunator_code="existing-registration",
        )
        team = old_apps.get_model("tab", "Team").objects.create(
            name="Existing U A", school=school, registration=registration,
            seed=0, team_code="existing-team-code", tiebreaker=123,
        )
        debater = old_apps.get_model("tab", "Debater").objects.create(
            name="Existing speaker", email="existing-speaker@example.com",
            novice_status=0,
        )
        team.debaters.add(debater)
        log_model = old_apps.get_model(
            "registration", "RegistrationConfirmationEmailLog",
        )
        log_model.objects.create(
            registration=registration, email=registration.email,
        )
        with mock.patch("mittab.libs.email_service.EmailService.send_bulk") as sender:
            MigrationExecutor(connection).migrate(latest)
            sender.assert_not_called()
        upgraded = Team.objects.get(pk=team.pk)
        assert upgraded.team_code == "existing-team-code"
        assert upgraded.registration.herokunator_code == "existing-registration"
        assert upgraded.debaters.get().pk == debater.pk
        assert upgraded.code_email_history_unknown
        assert upgraded.code_email_baseline == team_email_snapshot(
            upgraded, "Tournament")
        assert not TeamCodeEmailLog.objects.exists()
        assert RegistrationConfirmationEmailLog.objects.get().email_payload == {}
        assert not Team.objects.create(
            name="New U A", school_id=school.pk, seed=0,
        ).code_email_history_unknown
    finally:
        MigrationExecutor(connection).migrate(latest)
