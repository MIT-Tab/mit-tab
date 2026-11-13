"""Verify extrema independently by enumerating winners of actual paired teams."""

from itertools import product

import pytest

from mittab.apps.tab.models import School, TabSettings, Team
from mittab.apps.tab.tournament_calculator import calculator_context, record_bounds


def brute_force_next_round(losses):
    """Pair adjacent teams sorted by record and try every match winner."""
    states = set()
    matches = list(zip(losses[::2], losses[1::2]))
    for winners in product((0, 1), repeat=len(matches)):
        following = []
        for (first, second), winner in zip(matches, winners):
            following.extend((first + winner, second + (1 - winner)))
        if len(losses) % 2:
            following.append(losses[-1])  # Bottom-bracket bye counts as a win.
        states.add(tuple(sorted(following)))
    return states


@pytest.mark.parametrize("team_count", range(0, 11))
@pytest.mark.parametrize("num_rounds", range(1, 7))
def test_bounds_equal_exhaustive_individual_match_results(team_count, num_rounds):
    states = {(0,) * team_count}
    for _ in range(num_rounds):
        states = {
            following for state in states for following in brute_force_next_round(state)
        }
    totals = [sum(loss <= 1 for loss in state) for state in states]
    best, worst = record_bounds(team_count, num_rounds)
    assert sum(best[-1][:2]) == min(totals)
    assert sum(worst[-1][:2]) == max(totals)
    # Every displayed intermediate column belongs to one possible whole scenario.
    for path in (best, worst):
        previous = (0,) * team_count
        for number, column in enumerate(path, 1):
            assert len(column) == number + 1
            assert sum(column) == team_count
            individual_losses = tuple(
                loss for loss, count in enumerate(column) for _ in range(count)
            )
            assert individual_losses in brute_force_next_round(previous)
            previous = individual_losses


@pytest.mark.parametrize(
    "team_count,minimum,maximum", [(28, 4, 6), (53, 9, 11), (117, 21, 23)]
)
def test_five_round_bounds_for_reference_fields(team_count, minimum, maximum):
    assert calculator_context(team_count, 5, "best")["top_bracket_count"] == minimum
    assert calculator_context(team_count, 5, "worst")["top_bracket_count"] == maximum


@pytest.mark.django_db
def test_scenario_and_team_toggles_preserve_each_other_and_round_count(client):
    school = School.objects.create(name="Calculator school")
    for number in range(28):
        Team.objects.create(
            name=f"Calculator team {number}",
            school=school,
            seed=0,
            checked_in=number < 20,
        )
    TabSettings.set("teams_public", 1)
    TabSettings.set("tot_rounds", 5)
    url = "/public/teams/expected-records/"
    best = client.get(url)
    worst = client.get(url, {"case": "worst"})
    assert best.context["case"] == "best"
    assert best.context["top_bracket_count"] == 4
    assert worst.context["top_bracket_count"] == 6
    assert best.context["value"] == worst.context["value"] == 13
    checked = client.get(url, {"case": "worst", "teams": "checked-in"})
    assert checked.context["team_count"] == 20
    assert b"?teams=total&amp;case=worst" in checked.content
    assert b"?teams=checked-in&amp;case=best" in checked.content
    TabSettings.set("tot_rounds", 6)
    six_rounds = client.get(url, {"case": "worst", "teams": "checked-in"})
    assert six_rounds.context["round_numbers"] == [6, 5, 4, 3, 2, 1]
    assert six_rounds.context["top_bracket_count"] == sum(
        record_bounds(20, 6)[1][-1][:2]
    )
    assert client.get(url, {"case": "invalid"}).context["case"] == "best"
    TabSettings.set("teams_public", 0)
    assert client.get(url, {"case": "worst"}).status_code == 302
