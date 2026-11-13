"""Public roster history, estimates, and their privacy boundaries."""

# pylint: disable=redefined-outer-name,unused-argument

from datetime import datetime, timedelta, timezone as datetime_timezone

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import caches
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from mittab.apps.registration.models import RegistrationConfig
from mittab.apps.registration.services import sync_judge_expected_checkins
from mittab.apps.tab.auth_roles import APDA_BOARD_GROUP_NAME
from mittab.apps.tab.models import (
    AuditEvent,
    Debater,
    Judge,
    Outround,
    PublicRosterChange,
    Round,
    School,
    Scratch,
    TabSettings,
    Team,
)
from mittab.apps.tab.tournament_calculator import calculator_context, tournament_value


@pytest.fixture
def roster(db):
    config = RegistrationConfig.get_or_create_active()
    school = School.objects.create(name="Original school")
    other = School.objects.create(name="Other school")
    debater = Debater.objects.create(
        name="Original debater", novice_status=0, school=school
    )
    team = Team.objects.create(
        name="Original team", school=school, seed=0, checked_in=False
    )
    team.debaters.add(debater)
    judge = Judge.objects.create(name="Original judge", rank=4)
    judge.schools.add(school)
    TabSettings.set("teams_public", 1)
    TabSettings.set("judges_public", 1)
    TabSettings.set("debaters_public", 1)
    TabSettings.set("tot_rounds", 5)
    caches["public"].clear()
    return config, school, other, debater, team, judge


def close_registration(config):
    config.allow_new_registrations = False
    config.save()


def messages():
    return "\n".join(PublicRosterChange.objects.values_list("message", flat=True))


def test_history_starts_only_after_closure(roster):
    config, _, _, debater, team, judge = roster
    judge.name = "Before closure"
    judge.save()
    assert not PublicRosterChange.objects.exists()
    # Not yet open does not count as closed.
    config.new_registrations_open_at = timezone.now() + timedelta(days=1)
    config.save()
    judge.name = "Before opening"
    judge.save()
    assert not PublicRosterChange.objects.exists()
    config.new_registrations_open_at = None
    config.new_registrations_close_at = timezone.now() - timedelta(minutes=1)
    config.save()
    judge.name = "After deadline"
    judge.save()
    assert "Before opening → After deadline" in messages()
    assert "Before closure" not in messages()
    # Reopening pauses recording and retains existing public history.
    config.new_registrations_close_at = None
    config.save()
    count = PublicRosterChange.objects.count()
    debater.name = "During reopened registration"
    debater.save()
    team.name = "Team renamed"
    team.save()
    assert PublicRosterChange.objects.count() == count


def test_requested_changes_and_deletions_are_durable(roster):
    config, school, other, debater, team, judge = roster
    close_registration(config)
    added = Team.objects.create(name="Added team", school=school, seed=0)
    new_judge = Judge.objects.create(name="Added judge", rank=3)
    new_debater = Debater.objects.create(
        name="Replacement", novice_status=0, school=other
    )
    team.debaters.set([new_debater])
    new_debater.name = "Renamed debater"
    new_debater.school = school
    new_debater.save()
    judge.name = "Renamed judge"
    judge.save()
    judge.schools.set([other])
    team.hybrid_school = other
    team.save()
    sync_judge_expected_checkins(judge, [0, 1, 3])
    sync_judge_expected_checkins(judge, [1])
    added.delete()
    new_judge.delete()
    text = messages()
    for expected in (
        "Added team",
        "Added judge",
        "Removed team",
        "Removed judge",
        debater.name,
        "Replacement",
        "Renamed debater",
        "Renamed judge",
        "Other school",
        "Round 3 available",
        "Round 3 unavailable",
        "Outrounds available",
        "Outrounds unavailable",
    ):
        assert expected in text
    assert (
        "Original school → Other school" not in text
    )  # Team hybrid was previously empty.


def test_excluded_changes_never_enter_public_history(roster):
    config, _, _, debater, team, judge = roster
    close_registration(config)
    team.name = "Team name only"
    team.seed = Team.FULL_SEED
    team.break_preference = Team.NOVICE
    team.checked_in = True
    team.save()
    debater.novice_status = Debater.NOVICE
    debater.email = "private@example.com"
    debater.save()
    judge.rank = 9
    judge.reported_experience = 10
    judge.wing_only = True
    judge.email = "private-judge@example.com"
    judge.save()
    Scratch.objects.create(team=team, judge=judge, scratch_type=Scratch.TEAM_SCRATCH)
    assert not PublicRosterChange.objects.exists()


def test_reverse_memberships_clear_and_school_renames(roster):
    config, school, other, debater, team, judge = roster
    close_registration(config)
    debater.team_set.clear()
    debater.team_set.add(team)
    other.judge_set.add(judge)
    other.judge_set.clear()
    school.name = "Renamed affiliation"
    school.save()
    assert "Removed debater" in messages()
    assert "Added debater" in messages()
    assert "Removed affiliation" in messages()
    assert "Original school → Renamed affiliation" in messages()


def test_rolled_back_changes_are_not_logged(roster):
    config, _, _, _, _, judge = roster
    close_registration(config)
    with pytest.raises(ValueError), transaction.atomic():
        judge.name = "Rolled back"
        judge.save()
        raise ValueError("Rollback")
    assert not PublicRosterChange.objects.exists()


def test_partial_saves_and_duplicate_availability_do_not_log_false_changes(roster):
    config, _, _, _, _, judge = roster
    close_registration(config)
    judge.name = "Unsaved name"
    judge.rank = 5
    judge.save(update_fields=["rank"])
    assert not PublicRosterChange.objects.exists()
    sync_judge_expected_checkins(judge, [1, 2])
    count = PublicRosterChange.objects.count()
    sync_judge_expected_checkins(judge, [1, 2])
    assert PublicRosterChange.objects.count() == count
    assert "Unsaved name" not in messages()


def test_team_status_uses_debaters_and_never_logs_status_or_seed_changes(
    client, roster
):
    config, school, _, debater, team, _ = roster
    close_registration(config)
    assert team.novice_status_display() == "Varsity"
    debater.novice_status = Debater.NOVICE
    debater.save()
    # Registration defaults the break preference to varsity even for novices.
    assert team.break_preference == Team.VARSITY
    assert team.novice_status_display() == "Novice"
    team.seed = Team.HALF_SEED
    team.save()
    response = client.get(reverse("public_teams"))
    assert b"<td>Novice</td>" in response.content
    assert b"<td>Half Seed</td>" in response.content
    assert not PublicRosterChange.objects.exists()
    partner = Debater.objects.create(
        name="Varsity partner", school=school, novice_status=0
    )
    team.debaters.add(partner)
    assert team.novice_status_display() == "Varsity / Novice"


def test_batch_availability_changes_are_logged_but_checkins_are_not(client, roster):
    config, _, _, _, _, judge = roster
    close_registration(config)
    user = get_user_model().objects.create_superuser("roster_tab", password="password")
    client.force_login(user)
    TabSettings.set("start_tournament_wizard_completed", 1)
    payload = {
        "entity_type": "judge_expected",
        "action": "check_in",
        "entity_ids[]": [judge.pk],
        "rounds[]": [1, 3],
    }
    assert client.post(reverse("bulk_check_in"), payload).json() == {"success": True}
    assert "Round 1 available" in messages() and "Round 3 available" in messages()
    count = PublicRosterChange.objects.count()
    assert client.post(reverse("bulk_check_in"), payload).status_code == 200
    assert PublicRosterChange.objects.count() == count
    payload["entity_type"] = "judge"
    assert client.post(reverse("bulk_check_in"), payload).status_code == 200
    assert PublicRosterChange.objects.count() == count
    payload.update(entity_type="judge_expected", action="check_out")
    assert client.post(reverse("bulk_check_in"), payload).status_code == 200
    assert "Round 1 unavailable" in messages() and "Round 3 unavailable" in messages()


@pytest.mark.parametrize("month", [1, 7])
def test_timestamps_use_fixed_est_even_in_summer(month):
    entry = PublicRosterChange(
        created_at=datetime(2026, month, 2, 17, 5, tzinfo=datetime_timezone.utc)
    )
    assert entry.timestamp_est.endswith("12:05 PM EST")


@pytest.mark.parametrize(
    "count,value",
    [
        (0, 0),
        (1, 8),
        (15, 8),
        (16, 12),
        (23, 12),
        (24, 13),
        (28, 13),
        (53, 16),
        (64, 18),
        (72, 19),
        (80, 20),
        (117, 20),
        (200, 20),
    ],
)
def test_apda_point_thresholds(count, value):
    assert tournament_value(count) == value


@pytest.mark.parametrize(
    "count,final",
    [
        (28, [1, 5, 8, 8, 5, 1]),
        (53, [2, 9, 16, 16, 9, 1]),
        (117, [4, 19, 36, 36, 19, 3]),
        (72, [3, 10, 23, 23, 10, 3]),
    ],
)
def test_records_match_reference_screenshots(count, final):
    context = calculator_context(count, 5)
    assert [row[0]["count"] for row in context["record_rows"]] == final
    for column in zip(*context["record_rows"]):
        assert sum(cell["count"] for cell in column if cell) == count


def test_awards_match_reference():
    assert calculator_context(28, 5)["toty"] == [
        ("Winner", 13),
        ("Finals", 9),
        ("Semis", 3.75),
        ("Quarters", 0.5),
    ]
    assert calculator_context(117, 5)["toty"][-1] == ("Octos", 1.5)
    assert ("Octos", 0.75) in calculator_context(72, 5)["toty"]
    assert all(label != "Octos" for label, _ in calculator_context(64, 5)["toty"])


def test_lists_and_calculator_visibility_toggle_and_round_count(client, roster):
    config, _, _, debater, team, judge = roster
    close_registration(config)
    debater.name = "Public debater rename"
    debater.save()
    judge.name = "Public judge rename"
    judge.save()
    teams = client.get(reverse("public_teams"))
    assert b"0 of 1 teams checked in" in teams.content
    assert b"0 teams" in teams.content and b"0 points" in teams.content
    assert b"Novice / Varsity" in teams.content and b"Seeding" in teams.content
    assert b"Public debater rename" in teams.content
    judges = client.get(reverse("public_judges"))
    assert b"Public judge rename" in judges.content
    assert all(
        set(row) == {"name", "affiliations", "availability"}
        for row in judges.context["judges"]
    )
    url = reverse("public_expected_records")
    assert url.encode() in teams.content
    assert url.encode() not in client.get(reverse("public_home")).content
    total = client.get(url)
    checked = client.get(url, {"teams": "checked-in"})
    assert total.context["team_count"] == 1
    assert client.post(url).status_code == 405
    assert total["Cache-Control"] == "private, no-store"
    assert checked.context["team_count"] == 0
    assert checked.context["value"] == 0
    team.checked_in = True
    team.save()
    TabSettings.set("tot_rounds", 6)
    updated = client.get(url, {"teams": "checked-in"})
    assert updated.context["team_count"] == 1
    assert updated.context["round_numbers"] == [6, 5, 4, 3, 2, 1]
    TabSettings.set("debaters_public", 0)
    assert b"Public debater rename" not in client.get(reverse("public_teams")).content
    TabSettings.set("teams_public", 0)
    assert client.get(url).status_code == 302
    assert client.get(url, {"teams": "checked-in"}).status_code == 302
    TabSettings.set("judges_public", 0)
    assert client.get(reverse("public_judges")).status_code == 302


def test_board_public_schema_ignores_protected_state_and_cannot_mutate(client, roster):
    config, _, _, _, team, judge = roster
    close_registration(config)
    user = get_user_model().objects.create_user(
        "roster_board", is_staff=True, is_superuser=True
    )
    group, _ = Group.objects.get_or_create(name=APDA_BOARD_GROUP_NAME)
    user.groups.add(group)
    client.force_login(user)
    assert client.get(reverse("public_expected_records")).status_code == 403
    # Open board access with completed final-round outcomes.
    other_team = Team.objects.create(name="Other team", school=team.school, seed=0)
    Round.objects.create(
        round_number=5, gov_team=team, opp_team=other_team, victor=Round.GOV
    )
    urls = [
        reverse("public_teams"),
        reverse("public_judges"),
        reverse("public_expected_records"),
    ]
    responses = [client.get(url) for url in urls]
    assert all(response.status_code == 200 for response in responses)
    assert set(responses[2].context["record_rows"][0][0]) == {"record", "count"}
    judge.rank = 9
    judge.wing_only = True
    judge.save()
    Scratch.objects.create(team=team, judge=judge, scratch_type=Scratch.TEAM_SCRATCH)
    assert [response.content for response in responses] == [
        client.get(url).content for url in urls
    ]
    protected = {
        "judges": list(Judge.objects.values()),
        "scratches": list(Scratch.objects.values()),
        "audits": list(AuditEvent.objects.values()),
        "inrounds": list(Round.objects.values()),
        "outrounds": list(Outround.objects.values()),
    }
    for url in urls:
        assert client.post(url, {"rank": 0, "name": "Hacked"}).status_code == 302
    assert protected == {
        "judges": list(Judge.objects.values()),
        "scratches": list(Scratch.objects.values()),
        "audits": list(AuditEvent.objects.values()),
        "inrounds": list(Round.objects.values()),
        "outrounds": list(Outround.objects.values()),
    }
    TabSettings.set("teams_public", 0)
    assert client.get(urls[2]).status_code == 302
