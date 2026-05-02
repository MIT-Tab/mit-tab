from decimal import Decimal
from unittest import mock

import pytest

from mittab.apps.registration.forms import DEBATER_PREFIXES
from mittab.apps.registration.models import Registration
from mittab.apps.registration.views import MAX_TEAMS
from mittab.apps.tab.models import CheckIn, School, Team


def base_management(prefix, total, initial=0):
    max_value = str(MAX_TEAMS if prefix == "teams" else 1000)
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": max_value,
    }


def speaker(name, school, school_name, apda_id=""):
    return {
        "id": "",
        "name": name,
        "apda_id": apda_id,
        "novice_status": "0",
        "school": school,
        "school_name": school_name,
    }


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


@pytest.mark.django_db
@mock.patch("mittab.apps.registration.views.EmailService")
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
                speaker("Registration U Speaker 2", "__new__", "Hybrid School"),
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
    judge = registration.judges.first()
    assert judge.name == "Reg Judge"
    assert judge.rank == Decimal("7")
    assert judge.email == "judge@example.com"
    assert {school.name for school in judge.schools.all()} == {
        "Registration U",
        "Judge Hybrid",
    }
    checkins = set(
        CheckIn.objects.filter(judge=judge).values_list("round_number", flat=True)
    )
    assert checkins == {0, 1, 3}
    email_service.return_value.send_bulk.assert_called_once()
    email_request = email_service.return_value.send_bulk.call_args.args[0][0]
    assert email_request.to_address == "contact@example.com"
    assert registration.herokunator_code in email_request.text_body
    assert "http://testserver/registration/" in email_request.text_body
    assert "Registration U A" in email_request.text_body
    assert "Reg Judge" in email_request.text_body


@pytest.mark.django_db
@mock.patch("mittab.apps.registration.views.EmailService")
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
