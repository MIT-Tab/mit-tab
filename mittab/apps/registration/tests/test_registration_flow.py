import json
from decimal import Decimal
from unittest import mock

import pytest
from django.test import override_settings
from django.urls import reverse

from mittab.apps.registration.forms import DEBATER_PREFIXES
from mittab.apps.registration.models import (
    Registration,
    RegistrationChangeLog,
    RegistrationConfirmationEmailLog,
    RegistrationConfig,
)
from mittab.apps.registration.views import MAX_TEAMS
from mittab.apps.tab.models import (
    CheckIn,
    Debater,
    Judge,
    JudgeCodeEmailLog,
    JudgeExpectedCheckIn,
    School,
    Scratch,
    Team,
)


def base_management(prefix, total, initial=0):
    max_value = str(MAX_TEAMS if prefix == "teams" else 1000)
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": max_value,
    }


def speaker(name, school, school_name, apda_id="", email=None):
    if email is None:
        email = (
            f"{name.lower().replace(' ', '.')}@example.com"
            if name else ""
        )
    return {
        "id": "",
        "name": name,
        "email": email,
        "use_private_email": "",
        "apda_id": apda_id,
        "novice_status": "0",
        "school": school,
        "school_name": school_name,
    }


@pytest.mark.django_db
@mock.patch("mittab.apps.registration.emails.EmailService")
def test_registration_fills_missing_debater_email_from_private_lookup(
    email_service, client
):
    email_service.return_value.send_bulk.return_value = 1
    private_speaker = speaker(
        "Private Lookup Speaker",
        "apda:123",
        "Registration U",
        apda_id="1000",
        email="",
    )
    private_speaker["use_private_email"] = "on"
    teams = [
        team_entry(
            0,
            "Registration U A",
            [
                private_speaker,
                speaker(
                    "Manual Email Speaker",
                    "apda:123",
                    "Registration U",
                    apda_id="1001",
                    email="manual@example.com",
                ),
            ],
        )
    ]
    payload = registration_payload(
        "apda:123",
        "Registration U",
        "contact@example.com",
        teams,
        [],
    )

    with mock.patch(
        "mittab.apps.registration.services.fetch_private_debater_emails",
        return_value={1000: "private@example.com"},
    ) as fetch_private:
        response = client.post("/registration/", data=payload)

    assert response.status_code == 302
    fetch_private.assert_called_once_with([1000])
    debater = Debater.objects.get(name="Private Lookup Speaker")
    assert debater.email == "private@example.com"


@pytest.mark.django_db
@mock.patch("mittab.apps.registration.emails.EmailService")
def test_registration_treats_masked_email_value_as_use_email_on_file(
    email_service, client
):
    """If the masked prefill value reaches the server (e.g. JS didn't scrub it
    on submit), the form should drop the masked value and fall back to the
    private email lookup rather than rejecting it as invalid."""
    email_service.return_value.send_bulk.return_value = 1
    private_speaker = speaker(
        "Private Lookup Speaker",
        "apda:123",
        "Registration U",
        apda_id="1000",
        email="jo***s@example.com",
    )
    private_speaker["use_private_email"] = "on"
    teams = [
        team_entry(
            0,
            "Registration U A",
            [
                private_speaker,
                speaker(
                    "Manual Email Speaker",
                    "apda:123",
                    "Registration U",
                    apda_id="1001",
                    email="manual@example.com",
                ),
            ],
        )
    ]
    payload = registration_payload(
        "apda:123", "Registration U", "contact@example.com", teams, [],
    )

    with mock.patch(
        "mittab.apps.registration.services.fetch_private_debater_emails",
        return_value={1000: "private@example.com"},
    ) as fetch_private:
        response = client.post("/registration/", data=payload)

    assert response.status_code == 302
    fetch_private.assert_called_once_with([1000])
    debater = Debater.objects.get(name="Private Lookup Speaker")
    assert debater.email == "private@example.com"


def team_entry(index, name, speakers, seed_choice=Team.UNSEEDED,
               team_school_source="debater_one"):
    base = {
        f"teams-{index}-team_id": "",
        f"teams-{index}-name": name,
        f"teams-{index}-seed_choice": str(seed_choice),
        f"teams-{index}-DELETE": "",
        f"teams-{index}-team_school_source": team_school_source,
    }
    base.update(
        {
            f"teams-{index}-{prefix}_{field}": value
            for prefix, details in zip(DEBATER_PREFIXES, speakers)
            for field, value in details.items()
        }
    )
    return base


def blank_team_entry(index, school="apda:50", school_name="Test School"):
    return team_entry(
        index,
        "",
        [
            speaker("", school, school_name),
            speaker("", school, school_name),
        ],
    )


def judge_entry(index, name, email, experience, availability_rounds=None, schools=None):
    return {
        f"judges-{index}-registration_judge_id": "",
        f"judges-{index}-judge_id": "",
        f"judges-{index}-name": name,
        f"judges-{index}-email": email,
        f"judges-{index}-experience": str(experience),
        f"judges-{index}-DELETE": "",
        **{
            (
                f"judges-{index}-availability_outround"
                if round_number == 0
                else f"judges-{index}-availability_round_{round_number}"
            ): "on"
            for round_number in (availability_rounds or [])
        },
        f"judges-{index}-schools": schools or [],
    }


def registration_payload(school, school_name, email, teams, judges):
    data = {"school": school, "school_name": school_name, "email": email}
    data.update(base_management("teams", len(teams)))
    data.update(base_management("judges", len(judges)))
    for entry in teams + judges:
        data.update(entry)
    return data


@override_settings(
    BLACK_ROD_API_BASE_URL="https://black-rod.test",
    BLACK_ROD_PRIVATE_API_TOKEN="private-token",
)
@pytest.mark.django_db
def test_debater_email_status_returns_only_masked_private_email(client):
    debaters_response = mock.Mock()
    debaters_response.ok = True
    debaters_response.json.return_value = {
        "debaters": [{"id": 1234, "name": "Private Debater"}],
    }
    response_mock = mock.Mock()
    response_mock.ok = True
    response_mock.json.return_value = {
        "debaters": [{"id": 1234, "email": "debater@example.com"}],
    }
    with (
        mock.patch(
            "mittab.apps.registration.views.requests.get",
            return_value=debaters_response,
        ) as get,
        mock.patch(
            "mittab.apps.registration.services.requests.post",
            return_value=response_mock,
        ) as post,
    ):
        response = client.post(
            "/registration/api/debater-email-status/",
            data=json.dumps({"debater_id": 1234, "school_id": 55}),
            content_type="application/json",
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": 1234,
        "has_email": True,
        "masked_email": "de***@example.com",
    }
    get.assert_called_once_with("https://black-rod.test/api/debaters/55/", timeout=10)
    post.assert_called_once_with(
        "https://black-rod.test/api/private/debater-emails/",
        json={"debater_ids": [1234]},
        headers={"Authorization": "Bearer private-token"},
        timeout=10,
    )


@override_settings(BLACK_ROD_PRIVATE_API_TOKEN="")
@pytest.mark.django_db
def test_debater_email_status_degrades_when_token_is_missing(client):
    debaters_response = mock.Mock()
    debaters_response.ok = True
    debaters_response.json.return_value = {
        "debaters": [{"id": 1234, "name": "Private Debater"}],
    }
    with (
        mock.patch(
            "mittab.apps.registration.views.requests.get",
            return_value=debaters_response,
        ),
        mock.patch("mittab.apps.registration.services.requests.post") as post,
    ):
        response = client.post(
            "/registration/api/debater-email-status/",
            data=json.dumps({"debater_id": 1234, "school_id": 55}),
            content_type="application/json",
        )

    assert response.status_code == 200
    assert response.json() == {"id": 1234, "has_email": False, "masked_email": ""}
    post.assert_not_called()


@pytest.mark.django_db
def test_debater_email_status_is_blocked_when_registration_is_closed(client):
    config = RegistrationConfig.get_or_create_active()
    config.allow_new_registrations = False
    config.save()

    response = client.post(
        "/registration/api/debater-email-status/",
        data=json.dumps({"debater_id": 1234, "school_id": 55}),
        content_type="application/json",
    )

    assert response.status_code == 403


@pytest.mark.django_db
@mock.patch("mittab.apps.registration.emails.EmailService")
def test_registration_flow_creates_objects(email_service, client):
    email_service.return_value.send_bulk.return_value = 1
    School.objects.create(name="Judge Hybrid", apda_id=999)
    teams = [
        team_entry(
            0,
            "Registration U A",
            [
                speaker(
                    "Registration U Speaker 1",
                    "apda:123",
                    "Registration U",
                    apda_id="1000",
                ),
                speaker(
                    "Registration U Speaker 2",
                    "custom:Hybrid%20School",
                    "Hybrid School",
                ),
            ],
            seed_choice=Team.FREE_SEED,
        )
    ]
    judges = [
        judge_entry(
            0,
            "Reg Judge",
            "judge@example.com",
            7,
            availability_rounds=[0, 1, 3],
            schools=["apda:123", "apda:999"],
        )
    ]
    data = registration_payload(
        "apda:123", "Registration U", "contact@example.com", teams, judges
    )
    response = client.post("/registration/", data=data, follow=True)
    assert response.status_code == 200
    assert response.request["QUERY_STRING"] == "saved=1"
    assert b"Registration saved" in response.content
    assert b"School And Contact" not in response.content
    registration = Registration.objects.first()
    assert registration.herokunator_code in response.request["PATH_INFO"]
    assert registration.email == "contact@example.com"
    team = (
        registration.teams.select_related("school", "hybrid_school")
        .prefetch_related("debaters")
        .first()
    )
    assert team.name == "Registration U A"
    assert team.seed == Team.FREE_SEED
    assert team.school == registration.school
    assert team.hybrid_school is None
    debater_schools = {
        d.name: (d.school.name if d.school else None) for d in team.debaters.all()
    }
    assert debater_schools["Registration U Speaker 1"] == "Registration U"
    assert debater_schools["Registration U Speaker 2"] == "Hybrid School"
    debater_emails = {
        d.name: d.email for d in team.debaters.all()
    }
    assert (
        debater_emails["Registration U Speaker 1"] ==
        "registration.u.speaker.1@example.com"
    )
    assert (
        debater_emails["Registration U Speaker 2"] ==
        "registration.u.speaker.2@example.com"
    )
    judge = registration.judges.first()
    assert judge.name == "Reg Judge"
    assert judge.rank == Decimal("7")
    assert judge.email == "judge@example.com"
    assert {school.name for school in judge.schools.all()} == {
        "Registration U",
        "Judge Hybrid",
    }
    expected_checkins = set(
        JudgeExpectedCheckIn.objects.filter(judge=judge).values_list(
            "round_number",
            flat=True,
        )
    )
    assert expected_checkins == {0, 1, 3}
    assert not CheckIn.objects.filter(judge=judge).exists()
    assert email_service.return_value.send_bulk.call_count == 3
    email_request = (
        email_service.return_value.send_bulk.call_args_list[0].args[0][0]
    )
    assert email_request.to_address == "contact@example.com"
    assert registration.herokunator_code in email_request.text_body
    assert "http://testserver/registration/" in email_request.text_body
    assert team.team_code not in email_request.text_body
    assert "http://testserver/team_portal/" not in email_request.text_body
    assert "Registration U A" in email_request.text_body
    assert "Reg Judge" in email_request.text_body
    assert "judge@example.com" not in email_request.text_body
    assert judge.ballot_code not in email_request.text_body
    team_portal_requests = (
        email_service.return_value.send_bulk.call_args_list[1].args[0]
    )
    assert {
        request.to_address for request in team_portal_requests
    } == {
        "registration.u.speaker.1@example.com",
        "registration.u.speaker.2@example.com",
    }
    assert all(
        "http://testserver/team_portal/" in request.text_body
        for request in team_portal_requests
    )
    assert all(
        "Team code:" not in request.text_body
        for request in team_portal_requests
    )
    judge_code_request = (
        email_service.return_value.send_bulk.call_args_list[2].args[0][0]
    )
    assert judge_code_request.to_address == "judge@example.com"
    assert "registered as a judge for" in judge_code_request.text_body
    assert judge.ballot_code in judge_code_request.text_body
    assert "Your current availability is: Outrounds, Round 1, Round 3" in (
        judge_code_request.text_body
    )
    assert (
        f"http://testserver/public/e-ballots/{judge.ballot_code}/"
        in judge_code_request.text_body
    )
    judge_code_log = JudgeCodeEmailLog.objects.get(judge=judge)
    assert judge_code_log.email == "judge@example.com"
    assert judge_code_log.source == JudgeCodeEmailLog.SOURCE_REGISTRATION
    assert judge_code_log.ballot_code == judge.ballot_code
    confirmation_log = RegistrationConfirmationEmailLog.objects.get(
        registration=registration,
    )
    assert confirmation_log.email == "contact@example.com"
    assert confirmation_log.successful
    log = RegistrationChangeLog.objects.get()
    assert log.action == RegistrationChangeLog.CREATED
    assert log.registration == registration
    assert log.snapshot["school"]["name"] == "Registration U"
    assert log.snapshot["teams"][0]["debaters"][0]["apda_id"] == 1000
    assert (
        log.snapshot["teams"][0]["debaters"][0]["email"] ==
        "registration.u.speaker.1@example.com"
    )
    assert "email" not in log.snapshot["judges"][0]
    assert f"(Code: {judge.ballot_code})".encode() not in response.content


@pytest.mark.django_db
@mock.patch("mittab.apps.registration.emails.EmailService")
def test_custom_school_reuses_existing_name(email_service, client):
    email_service.return_value.send_bulk.return_value = 1
    school = School.objects.create(name="Existing U", apda_id=-1)
    judges = [
        judge_entry(
            0,
            "Existing Judge",
            "judge@example.com",
            5,
            availability_rounds=[1],
            schools=["custom:Existing%20U"],
        )
    ]
    data = registration_payload(
        "custom:Existing%20U",
        "",
        "existing@example.com",
        [],
        judges,
    )

    response = client.post("/registration/", data=data, follow=True)

    assert response.status_code == 200
    registration = Registration.objects.get()
    assert registration.school == school
    assert School.objects.filter(name__iexact="Existing U").count() == 1
    school.refresh_from_db()
    assert school.apda_id == -1


@pytest.mark.django_db
def test_registration_requires_single_free_seed(client):
    speakers = [
        speaker("Speaker 1", "apda:50", "Test School"),
        speaker("Speaker 2", "apda:50", "Test School"),
    ]
    more_speakers = [
        speaker("Speaker 3", "apda:50", "Test School"),
        speaker("Speaker 4", "apda:50", "Test School"),
    ]
    teams = [
        team_entry(0, "Team One", speakers, seed_choice=Team.FREE_SEED),
        team_entry(1, "Team Two", more_speakers, seed_choice=Team.FREE_SEED),
    ]
    data = registration_payload("apda:50", "Test School", "team@example.com", teams, [])
    response = client.post("/registration/", data=data)
    assert response.status_code == 200
    assert b"Select at most one free seed" in response.content


@pytest.mark.django_db
def test_registration_requires_debater_emails(client):
    teams = [
        team_entry(
            0,
            "No Email Team",
            [
                speaker("Speaker 1", "apda:50", "Test School", email=""),
                speaker("Speaker 2", "apda:50", "Test School"),
            ],
        )
    ]
    data = registration_payload("apda:50", "Test School", "team@example.com", teams, [])

    response = client.post("/registration/", data=data)

    assert response.status_code == 200
    assert b"Each debater needs an email" in response.content
    assert not Registration.objects.exists()


@pytest.mark.django_db
def test_registration_ignores_blank_prefilled_team_rows(client):
    teams = [
        team_entry(
            0,
            "Started Team",
            [
                speaker("Only Speaker", "apda:50", "Test School"),
                speaker("", "apda:50", "Test School"),
            ],
        ),
        blank_team_entry(1),
        blank_team_entry(2),
        blank_team_entry(3),
    ]
    data = registration_payload("apda:50", "Test School", "team@example.com", teams, [])

    response = client.post("/registration/", data=data)

    assert response.status_code == 200
    assert response.content.count(b"Each team needs two debaters") == 1


@pytest.mark.django_db
@mock.patch("mittab.apps.registration.emails.EmailService")
def test_registration_edit_logs_changes(email_service, client):
    email_service.return_value.send_bulk.return_value = 1
    teams = [
        team_entry(
            0,
            "Original Team",
            [
                speaker("Speaker A", "apda:77", "Log School"),
                speaker("Speaker B", "apda:77", "Log School"),
            ],
        )
    ]
    data = registration_payload(
        "apda:77", "Log School", "before@example.com", teams, []
    )
    response = client.post("/registration/", data=data, follow=True)
    assert response.status_code == 200
    registration = Registration.objects.get()
    team = registration.teams.first()
    debaters = list(team.debaters.order_by("name"))

    edited_team = team_entry(
        0,
        "Updated Team",
        [
            {
                **speaker("Speaker A", "apda:77", "Log School"),
                "id": str(debaters[0].pk),
            },
            {
                **speaker("Speaker B", "apda:77", "Log School"),
                "id": str(debaters[1].pk),
            },
        ],
    )
    edited_team["teams-0-team_id"] = str(team.pk)
    edited = registration_payload(
        "apda:77", "Log School", "after@example.com", [edited_team], []
    )
    response = client.post(
        f"/registration/{registration.herokunator_code}/",
        data=edited,
        follow=True,
    )

    assert response.status_code == 200
    logs = list(RegistrationChangeLog.objects.order_by("created_at"))
    assert [log.action for log in logs] == [
        RegistrationChangeLog.CREATED,
        RegistrationChangeLog.UPDATED,
    ]
    assert "email" in logs[1].changes
    assert "teams" in logs[1].changes


@pytest.mark.django_db
@mock.patch("mittab.apps.registration.emails.EmailService")
def test_registration_edit_updates_judge_expected_checkins(email_service, client):
    email_service.return_value.send_bulk.return_value = 1
    judges = [
        judge_entry(
            0,
            "Schedule Judge",
            "judge@example.com",
            5,
            availability_rounds=[1, 2],
            schools=["apda:88"],
        )
    ]
    data = registration_payload(
        "apda:88",
        "Schedule School",
        "schedule@example.com",
        [],
        judges,
    )
    response = client.post("/registration/", data=data, follow=True)
    assert response.status_code == 200
    registration = Registration.objects.get()
    judge = registration.judges.get()

    response = client.get(f"/registration/{registration.herokunator_code}/")
    assert response.status_code == 200
    assert b'value="judge@example.com"' in response.content

    edited_judge = judge_entry(
        0,
        "Schedule Judge",
        "judge@example.com",
        5,
        availability_rounds=[2, 3],
        schools=["apda:88"],
    )
    edited_judge["judges-0-registration_judge_id"] = str(judge.pk)
    edited_judge["judges-0-judge_id"] = str(judge.pk)
    edited = registration_payload(
        "apda:88",
        "Schedule School",
        "schedule@example.com",
        [],
        [edited_judge],
    )

    response = client.post(
        f"/registration/{registration.herokunator_code}/",
        data=edited,
        follow=True,
    )

    assert response.status_code == 200
    assert set(
        JudgeExpectedCheckIn.objects.filter(judge=judge).values_list(
            "round_number",
            flat=True,
        )
    ) == {2, 3}
    assert not CheckIn.objects.filter(judge=judge).exists()


@pytest.mark.django_db
@mock.patch("mittab.apps.registration.emails.EmailService")
def test_registration_does_not_update_unrelated_debater_id(email_service, client):
    email_service.return_value.send_bulk.return_value = 1
    school = School.objects.create(name="Existing School", apda_id=501)
    unrelated = Debater.objects.create(
        name="Unrelated Person",
        novice_status=Debater.VARSITY,
        qualified=False,
        apda_id=9001,
        school=school,
    )
    teams = [
        team_entry(
            0,
            "Privacy Team",
            [
                {
                    **speaker("Submitted Speaker", "apda:501", "Existing School"),
                    "id": str(unrelated.pk),
                },
                speaker("Second Speaker", "apda:501", "Existing School"),
            ],
        )
    ]
    data = registration_payload(
        "apda:501", "Existing School", "privacy@example.com", teams, []
    )

    response = client.post("/registration/", data=data, follow=True)

    assert response.status_code == 200
    unrelated.refresh_from_db()
    assert unrelated.name == "Unrelated Person"
    registration = Registration.objects.get()
    names = {
        debater.name
        for team in registration.teams.all()
        for debater in team.debaters.all()
    }
    assert "Submitted Speaker" in names


@pytest.mark.django_db
def test_team_portal_updates_team_name_and_disc_scratches(client):
    config = RegistrationConfig.get_or_create_active()
    config.team_name_changes_allowed = True
    config.disc_scratches_open = True
    config.disc_scratch_quantity = 2
    config.save()
    school = School.objects.create(name="Portal U")
    team = Team.objects.create(
        name="Portal Team",
        school=school,
        seed=Team.UNSEEDED,
    )
    judge_one = Judge.objects.create(name="Judge One", rank=5)
    judge_two = Judge.objects.create(name="Judge Two", rank=6)
    judge_three = Judge.objects.create(name="Judge Three", rank=7)

    response = client.post(
        reverse("team_portal_search"),
        {"team_code": team.team_code.lower()},
    )

    assert response.status_code == 302
    assert response.url == reverse("team_portal", args=[team.team_code])

    response = client.post(
        reverse("team_portal", args=[team.team_code]),
        {"action": "update_name", "name": "Portal Team Renamed"},
        follow=True,
    )

    assert response.status_code == 200
    team.refresh_from_db()
    assert team.name == "Portal Team Renamed"

    response = client.post(
        reverse("team_portal", args=[team.team_code]),
        {
            "action": "update_scratches",
            "scratch_0": str(judge_one.pk),
            "scratch_1": str(judge_two.pk),
            "scratch_2": str(judge_three.pk),
        },
        follow=True,
    )

    assert response.status_code == 200
    assert set(
        Scratch.objects.filter(team=team).values_list("judge_id", flat=True)
    ) == {judge_one.pk, judge_two.pk}

    response = client.post(
        reverse("team_portal", args=[team.team_code]),
        {
            "action": "update_scratches",
            "scratch_0": str(judge_three.pk),
            "scratch_1": "",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert set(
        Scratch.objects.filter(team=team).values_list("judge_id", flat=True)
    ) == {judge_three.pk}


@pytest.mark.django_db
def test_team_portal_respects_closed_settings(client):
    config = RegistrationConfig.get_or_create_active()
    config.team_name_changes_allowed = False
    config.disc_scratches_open = False
    config.disc_scratch_quantity = 1
    config.save()
    school = School.objects.create(name="Closed Portal U")
    team = Team.objects.create(
        name="Closed Portal Team",
        school=school,
        seed=Team.UNSEEDED,
    )
    judge = Judge.objects.create(name="Closed Portal Judge", rank=5)

    response = client.post(
        reverse("team_portal", args=[team.team_code]),
        {"action": "update_name", "name": "Should Not Save"},
    )

    assert response.status_code == 200
    team.refresh_from_db()
    assert team.name == "Closed Portal Team"

    response = client.post(
        reverse("team_portal", args=[team.team_code]),
        {"action": "update_scratches", "scratch_0": str(judge.pk)},
    )

    assert response.status_code == 200
    assert not Scratch.objects.filter(team=team).exists()
