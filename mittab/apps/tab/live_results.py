from datetime import timedelta

from django.db import transaction
from django.db.models import Max, Min
from django.utils import timezone

from mittab.apps.tab.models import (
    BreakingTeam, Outround, PublishedOutroundStage, PublishedTeamPlacement,
    Round, TabSettings,
)
from mittab.apps.tab.public_pages import released_outrounds


def published_team_placements(division):
    """Keep each released stage's points floor while later rounds are prepared.

    Store only public team IDs and placements, never pairings or judge data.
    Releasing a stage again replaces that stage's snapshot to permit corrections.
    """
    stages = PublishedOutroundStage.objects.filter(type_of_round=division)
    rounds = list(released_outrounds(division).values(
        "num_teams", "gov_team_id", "opp_team_id", "victor",
    ))
    opening_size = max(
        list(stages.values_list("num_teams", flat=True))
        + [round_obj["num_teams"] for round_obj in rounds], default=0,
    )
    for size in {round_obj["num_teams"] for round_obj in rounds}:
        placements = {}
        for round_obj in rounds:
            if round_obj["num_teams"] != size:
                continue
            floor = size // 2 + 1
            winner_floor = 1 if size <= 2 else size // 4 + 1
            winner_id = None
            if round_obj["victor"] in (Outround.GOV, Outround.GOV_VIA_FORFEIT):
                winner_id = round_obj["gov_team_id"]
            elif round_obj["victor"] in (Outround.OPP, Outround.OPP_VIA_FORFEIT):
                winner_id = round_obj["opp_team_id"]
            for team_id in (round_obj["gov_team_id"], round_obj["opp_team_id"]):
                placements[team_id] = winner_floor if team_id == winner_id else floor
        if size == opening_size:
            bye_ids = BreakingTeam.objects.filter(type_of_team=division).exclude(
                team_id__in=placements,
            ).values_list("team_id", flat=True)
            for team_id in bye_ids:
                placements[team_id] = 1 if size <= 2 else size // 4 + 1
        _save_stage(division, size, placements)
    return dict(PublishedTeamPlacement.objects.filter(
        stage__type_of_round=division,
    ).values("team_id").annotate(best=Min("place")).values_list("team_id", "best"))


def _save_stage(division, size, placements):
    # Serialize concurrent refreshes, including the first publication. Replace
    # the whole stage atomically so readers cannot see a partial correction.
    with transaction.atomic():
        stage, _created = PublishedOutroundStage.objects.get_or_create(
            type_of_round=division, num_teams=size,
        )
        stage = PublishedOutroundStage.objects.select_for_update().get(pk=stage.pk)
        if dict(stage.placements.values_list("team_id", "place")) == placements:
            return
        stage.placements.all().delete()
        PublishedTeamPlacement.objects.bulk_create([
            PublishedTeamPlacement(stage=stage, team_id=team_id, place=place)
            for team_id, place in placements.items()
        ])


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

