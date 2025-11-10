from decimal import Decimal

import pytest

from mittab.apps.registration.models import Registration, RegistrationTeamMember
from mittab.apps.registration.views import MAX_TEAMS
from mittab.apps.tab.models import Team


def base_management(prefix, total, initial=0):
    max_value = str(MAX_TEAMS if prefix == "teams" else 1000)
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": max_value,
    }


@pytest.mark.django_db
def test_registration_flow_creates_objects(client):
    data = {
        "school": "apda:123",
        "school_name": "Registration U",
        "email": "contact@example.com",
    }
    data.update(base_management("teams", 1))
    data.update(base_management("judges", 1))
    data.update(
        {
            "teams-0-registration_team_id": "",
            "teams-0-team_id": "",
            "teams-0-name": "Registration U A",
            "teams-0-is_free_seed": "on",
            "teams-0-seed_choice": str(Team.FULL_SEED),
            "teams-0-debater_one_id": "",
            "teams-0-debater_one_name": "Registration U Speaker 1",
            "teams-0-debater_one_apda_id": "1000",
            "teams-0-debater_one_novice_status": "0",
            "teams-0-debater_one_school": "apda:123",
            "teams-0-debater_one_school_name": "Registration U",
            "teams-0-debater_two_id": "",
            "teams-0-debater_two_name": "Registration U Speaker 2",
            "teams-0-debater_two_apda_id": "",
            "teams-0-debater_two_novice_status": "0",
            "teams-0-debater_two_school": "__new__",
            "teams-0-debater_two_school_name": "Hybrid School",
            "teams-0-DELETE": "",
            "judges-0-registration_judge_id": "",
            "judges-0-judge_id": "",
            "judges-0-name": "Reg Judge",
            "judges-0-email": "judge@example.com",
            "judges-0-experience": "7",
            "judges-0-DELETE": "",
        }
    )
    response = client.post("/registration/", data=data, follow=True)
    assert response.status_code == 200
    registration = Registration.objects.first()
    assert registration.herokunator_code in response.request["PATH_INFO"]
    assert registration.email == "contact@example.com"
    reg_team = registration.teams.select_related("team").first()
    team = reg_team.team
    assert team.name == "Registration U A"
    assert team.seed == Team.FREE_SEED
    members = list(
        RegistrationTeamMember.objects.filter(registration_team=reg_team)
        .select_related("school")
        .order_by("position")
    )
    assert members[0].school == registration.school
    assert members[1].school.name == "Hybrid School"
    judge_relation = registration.judges.select_related("judge").first()
    assert judge_relation.judge.name == "Reg Judge"
    assert judge_relation.judge.rank == Decimal("7")
    assert judge_relation.judge.email == "judge@example.com"


@pytest.mark.django_db
def test_registration_requires_single_free_seed(client):
    data = {
        "school": "apda:50",
        "school_name": "Test School",
        "email": "team@example.com",
    }
    data.update(base_management("teams", 2))
    data.update(base_management("judges", 0))
    data.update(
        {
            "teams-0-registration_team_id": "",
            "teams-0-team_id": "",
            "teams-0-name": "Team One",
            "teams-0-seed_choice": str(Team.UNSEEDED),
            "teams-0-debater_one_id": "",
            "teams-0-debater_one_name": "Speaker 1",
            "teams-0-debater_one_apda_id": "",
            "teams-0-debater_one_novice_status": "0",
            "teams-0-debater_one_school": "apda:50",
            "teams-0-debater_one_school_name": "Test School",
            "teams-0-debater_two_id": "",
            "teams-0-debater_two_name": "Speaker 2",
            "teams-0-debater_two_apda_id": "",
            "teams-0-debater_two_novice_status": "0",
            "teams-0-debater_two_school": "apda:50",
            "teams-0-debater_two_school_name": "Test School",
            "teams-0-DELETE": "",
            "teams-1-registration_team_id": "",
            "teams-1-team_id": "",
            "teams-1-name": "Team Two",
            "teams-1-seed_choice": str(Team.UNSEEDED),
            "teams-1-debater_one_id": "",
            "teams-1-debater_one_name": "Speaker 3",
            "teams-1-debater_one_apda_id": "",
            "teams-1-debater_one_novice_status": "0",
            "teams-1-debater_one_school": "apda:50",
            "teams-1-debater_one_school_name": "Test School",
            "teams-1-debater_two_id": "",
            "teams-1-debater_two_name": "Speaker 4",
            "teams-1-debater_two_apda_id": "",
            "teams-1-debater_two_novice_status": "0",
            "teams-1-debater_two_school": "apda:50",
            "teams-1-debater_two_school_name": "Test School",
            "teams-1-DELETE": "",
        }
    )
    response = client.post("/registration/", data=data)
    assert response.status_code == 200
    assert b"Select at most one free seed" in response.content
