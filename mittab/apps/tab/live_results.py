from datetime import timedelta
import json

from django.db.models import Max
from django.utils import timezone

from mittab.apps.tab.models import BreakingTeam, Outround, Round, TabSettings
from mittab.apps.tab.public_pages import released_outrounds


def published_team_placements(division):
    """Keep each released stage's points floor while later rounds are prepared.

    Store only public team IDs and placements, never pairings or judge data.
    Releasing a stage again replaces that stage's snapshot to permit corrections.
    """
    key = f"live_results_published_stages_{division}"
    stages = json.loads(TabSettings.get(key, "{}"))
    previous = json.dumps(stages, sort_keys=True)
    rounds = list(released_outrounds(division).select_related("gov_team", "opp_team"))
    for size in {round_obj.num_teams for round_obj in rounds}:
        placements = {}
        for round_obj in rounds:
            if round_obj.num_teams != size:
                continue
            floor = size // 2 + 1
            winner_floor = 1 if size <= 2 else size // 4 + 1
            for team_id in (round_obj.gov_team_id, round_obj.opp_team_id):
                place = winner_floor if (
                    round_obj.winner and team_id == round_obj.winner.pk
                ) else floor
                placements[str(team_id)] = place
        if size == max({int(stage) for stage in stages} | {
                round_obj.num_teams for round_obj in rounds}):
            bye_ids = BreakingTeam.objects.filter(type_of_team=division).exclude(
                team_id__in=placements,
            ).values_list("team_id", flat=True)
            for team_id in bye_ids:
                placements[str(team_id)] = 1 if size <= 2 else size // 4 + 1
        stages[str(size)] = placements
    serialized = json.dumps(stages, sort_keys=True)
    if serialized != previous:
        TabSettings.set(key, serialized)
    best = {}
    for placements in stages.values():
        for team_id, place in placements.items():
            team_id = int(team_id)
            best[team_id] = min(place, best.get(team_id, place))
    return best


def final_inround_completed_at():
    """Return when the last ballot in the final inround was first completed."""
    try:
        final_round = int(TabSettings.get("tot_rounds", 0) or 0)
    except (TypeError, ValueError):
        return None
    if final_round < 1:
        return None

    rounds = Round.objects.filter(round_number=final_round)
    if not rounds.exists() or rounds.filter(victor=Round.UNKNOWN).exists():
        return None
    if rounds.filter(result_entered_at__isnull=True).exists():
        return None
    return rounds.aggregate(completed_at=Max("result_entered_at"))["completed_at"]


def speaker_results_available(now=None):
    """Expose speakers after outround publication or a three-hour embargo."""
    if any(published_team_placements(division)
           for division in (Outround.VARSITY, Outround.NOVICE)):
        return True
    completed_at = final_inround_completed_at()
    if completed_at is None:
        return False
    return (now or timezone.now()) >= completed_at + timedelta(hours=3)

