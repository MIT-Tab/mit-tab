from mittab.apps.tab.debater_views import get_speaker_rankings
from mittab.apps.tab.models import Debater, Team, Outround


def get_speaker_awards_data(speaker_results):
    return [{"apda_id": result[0].apda_id,
             "tournament_id": result[0].pk}
            for result in speaker_results]


def get_team_placements_data(team_type):
    outrounds = Outround.objects.filter(
        type_of_round=team_type
    ).prefetch_related(
        "gov_team", "opp_team", "gov_team__debaters", "opp_team__debaters"
    ).order_by("num_teams")

    if not outrounds.exists():
        return []

    finals = outrounds.first()
    placement_results = []

    if finals.victor in [Outround.GOV, Outround.GOV_VIA_FORFEIT]:
        placement_results.append(finals.gov_team)
    else:
        placement_results.append(finals.opp_team)

    for outround in outrounds:
        if outround.gov_team not in placement_results:
            placement_results.append(outround.gov_team)
        else:
            placement_results.append(outround.opp_team)

    return [[{"apda_id": debater.apda_id,
              "tournament_id": debater.pk}
             for debater in team.debaters.all()]
            for team in placement_results]


def get_registered_debater_ids():
    """Get list of all registered debater APDA IDs, excluding -1."""
    return list(Debater.objects.exclude(
        apda_id=-1).values_list("apda_id", flat=True))


def get_new_debater_data():
    """Get data for debaters who are new (no APDA ID yet)."""
    return [
        {
            "name": debater["name"],
            "novice_status": debater["novice_status"],
            "school_id": debater["team__school__id"],
            "school_name": debater["team__school__name"],
            "debater_id": debater["pk"]
        }
        for debater in Debater.objects.filter(apda_id=-1)
        .select_related("school")
        .values("name", "novice_status", "team__school__id", "team__school__name", "pk")
    ]


def get_tournament_standings():
    """Get complete tournament standings data formatted for API consumption."""
    varsity_speakers, novice_speakers = get_speaker_rankings()
    varsity_speakers = varsity_speakers[:10]
    novice_speakers = novice_speakers[:10]

    return {
        "varsity_speaker_awards": get_speaker_awards_data(varsity_speakers),
        "novice_speaker_awards": get_speaker_awards_data(novice_speakers),
        "varsity_team_placements": get_team_placements_data(Team.VARSITY),
        "novice_team_placements": get_team_placements_data(Team.NOVICE),
        "registered_debater_ids": get_registered_debater_ids(),
        "new_debater_data": get_new_debater_data()
    }
