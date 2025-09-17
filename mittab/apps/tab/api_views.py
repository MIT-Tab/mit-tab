from django.http import JsonResponse
from django.db.models import Count

from mittab.apps.tab.debater_views import get_speaker_rankings
from mittab.apps.tab.models import Debater, Team, Outround
from mittab.libs.tab_logic.stats import tot_wins


def _get_teams_with_min_rounds(min_rounds=3):
    """Helper function to get teams with minimum number of rounds."""
    return Team.objects.annotate(
        total_rounds=Count("gov_team") + Count("opp_team")
    ).filter(total_rounds__gte=min_rounds).values_list("pk", flat=True)


def _get_team_placements_and_ids(team_type):
    """Helper function to get team placements and placing team IDs."""
    outrounds = Outround.objects.filter(
        type_of_round=team_type
    ).prefetch_related(
        "gov_team", "opp_team", "gov_team__debaters", "opp_team__debaters"
    ).order_by("num_teams")

    if not outrounds.exists():
        return [], set()

    finals = outrounds.first()
    placement_results = []
    placing_team_ids = set()

    if finals.victor in [Outround.GOV, Outround.GOV_VIA_FORFEIT]:
        placement_results.append(finals.gov_team)
        placing_team_ids.add(finals.gov_team.pk)
    else:
        placement_results.append(finals.opp_team)
        placing_team_ids.add(finals.opp_team.pk)

    for outround in outrounds:
        if outround.gov_team not in placement_results:
            placement_results.append(outround.gov_team)
            placing_team_ids.add(outround.gov_team.pk)
        else:
            placement_results.append(outround.opp_team)
            placing_team_ids.add(outround.opp_team.pk)

    # Add non-breaking teams with 4 wins
    all_teams = Team.objects.filter(
        break_preference=team_type
    ).prefetch_related("debaters")

    for team in all_teams:
        if tot_wins(team) == 4 and team.pk not in placing_team_ids:
            placement_results.append(team)
            placing_team_ids.add(team.pk)

    formatted_data = [[{"apda_id": debater.apda_id, "tournament_id": debater.pk}
                       for debater in team.debaters.all()]
                      for team in placement_results]

    return formatted_data, placing_team_ids

def varsity_speaker_awards_api(request):
    """API endpoint for varsity speaker awards."""
    varsity_speakers, _ = get_speaker_rankings()
    varsity_speakers = varsity_speakers[:10]
    data = [{"apda_id": result[0].apda_id, "tournament_id": result[0].pk}
            for result in varsity_speakers]
    return JsonResponse({"varsity_speaker_awards": data})


def novice_speaker_awards_api(request):
    """API endpoint for novice speaker awards."""
    _, novice_speakers = get_speaker_rankings()
    novice_speakers = novice_speakers[:10]
    data = [{"apda_id": result[0].apda_id, "tournament_id": result[0].pk}
            for result in novice_speakers]
    return JsonResponse({"novice_speaker_awards": data})


def varsity_team_placements_api(request):
    """API endpoint for varsity team placements."""
    placements, _ = _get_team_placements_and_ids(Team.VARSITY)
    return JsonResponse({"varsity_team_placements": placements})


def novice_team_placements_api(request):
    """API endpoint for novice team placements."""
    placements, _ = _get_team_placements_and_ids(Team.NOVICE)
    return JsonResponse({"novice_team_placements": placements})


def non_placing_teams_api(request):
    """API endpoint for non-placing teams."""
    _, varsity_placing_ids = _get_team_placements_and_ids(Team.VARSITY)
    _, novice_placing_ids = _get_team_placements_and_ids(Team.NOVICE)
    all_placing_team_ids = varsity_placing_ids | novice_placing_ids

    teams = Team.objects.prefetch_related("debaters").exclude(
        pk__in=all_placing_team_ids)
    data = [[{"apda_id": debater.apda_id, "tournament_id": debater.pk}
             for debater in team.debaters.all()]
            for team in teams]

    return JsonResponse({"non_placing_teams": data})


def new_debater_data_api(request):
    """API endpoint for new debater data."""
    teams_with_min_rounds = _get_teams_with_min_rounds()

    data = [
        {
            "name": debater["name"],
            "novice_status": debater["novice_status"],
            "school_id": debater["team__school__apda_id"],
            "school_name": debater["team__school__name"],
            "debater_id": debater["pk"]
        }
        for debater in Debater.objects.filter(apda_id=-1)
        .filter(team__pk__in=teams_with_min_rounds)
        .select_related("team__school")
        .values("name", "novice_status",
                "team__school__apda_id", "team__school__name", "pk")
    ]

    return JsonResponse({"new_debater_data": data})


def new_schools_api(request):
    """API endpoint for new schools data."""
    teams_with_min_rounds = _get_teams_with_min_rounds()

    school_names = set()
    debater_data = (
        Debater.objects.filter(apda_id=-1)
        .filter(team__pk__in=teams_with_min_rounds)
        .select_related("team__school")
        .values("team__school__name", "team__school__apda_id")
    )

    for debater in debater_data:
        if debater["team__school__apda_id"] == -1:
            school_names.add(debater["team__school__name"])

    return JsonResponse({"new_schools": list(school_names)})
