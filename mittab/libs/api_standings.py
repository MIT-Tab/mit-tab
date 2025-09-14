from mittab.apps.tab.debater_views import get_speaker_rankings
from mittab.apps.tab.models import Debater, Team, Outround


def get_speaker_awards_data(speaker_results):
    return [{"apda_id": result[0].apda_id,
             "tournament_id": result[0].pk}
            for result in speaker_results]


def get_team_placements_and_ids(team_type):
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
    else:
        placement_results.append(finals.opp_team)

    for outround in outrounds:
        if outround.gov_team:
            placing_team_ids.add(outround.gov_team.pk)
        if outround.opp_team:
            placing_team_ids.add(outround.opp_team.pk)
            
        if outround.gov_team not in placement_results:
            placement_results.append(outround.gov_team)
        else:
            placement_results.append(outround.opp_team)

    formatted_data = [[{"apda_id": debater.apda_id,
                        "tournament_id": debater.pk}
                       for debater in team.debaters.all()]
                      for team in placement_results]
    
    return formatted_data, placing_team_ids


def get_team_placements_data(team_type):
    placements, _ = get_team_placements_and_ids(team_type)
    return placements


def get_nonplacing_teams(placing_team_ids):
    teams = Team.objects.prefetch_related("debaters").exclude(pk__in=placing_team_ids)
    
    return [
        [
            {
                "apda_id": debater.apda_id,
                "tournament_id": debater.pk
            }
            for debater in team.debaters.all()
        ]
        for team in teams
    ]


def get_new_debater_data():
    """Get data for debaters who are new (no APDA ID yet)."""
    from django.db.models import Count
    
    teams_with_min_rounds = Team.objects.annotate(
        total_rounds=Count('gov_team') + Count('opp_team')
    ).filter(total_rounds__gte=3).values_list('pk', flat=True)
    
    return [
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
        .values("name", "novice_status", "team__school__apda_id", "team__school__name", "pk")
    ]


def get_new_schools_data():
    """Get data for schools that are new (school APDA ID is -1 for debaters with APDA ID -1)."""
    from django.db.models import Count
    
    # Use the same filtering logic as get_new_debater_data
    teams_with_min_rounds = Team.objects.annotate(
        total_rounds=Count('gov_team') + Count('opp_team')
    ).filter(total_rounds__gte=3).values_list('pk', flat=True)
    
    school_names = set()
    
    # Get debaters with apda_id=-1 from teams with at least 3 rounds
    debater_data = Debater.objects.filter(apda_id=-1)\
        .filter(team__pk__in=teams_with_min_rounds)\
        .select_related("team__school")\
        .values("team__school__name", "team__school__apda_id")
    
    for debater in debater_data:
        if debater["team__school__apda_id"] == -1:
            school_names.add(debater["team__school__name"])
    
    return list(school_names)


def get_varsity_speaker_awards():
    """Get varsity speaker awards data."""
    varsity_speakers, _ = get_speaker_rankings()
    varsity_speakers = varsity_speakers[:10]
    return get_speaker_awards_data(varsity_speakers)


def get_novice_speaker_awards():
    """Get novice speaker awards data."""
    _, novice_speakers = get_speaker_rankings()
    novice_speakers = novice_speakers[:10]
    return get_speaker_awards_data(novice_speakers)


def get_varsity_team_placements():
    """Get varsity team placements data."""
    placements, _ = get_team_placements_and_ids(Team.VARSITY)
    return placements


def get_novice_team_placements():
    """Get novice team placements data."""
    placements, _ = get_team_placements_and_ids(Team.NOVICE)
    return placements


def get_non_placing_teams():
    """Get non-placing teams data."""
    _, varsity_placing_ids = get_team_placements_and_ids(Team.VARSITY)
    _, novice_placing_ids = get_team_placements_and_ids(Team.NOVICE)
    all_placing_team_ids = varsity_placing_ids | novice_placing_ids
    return get_nonplacing_teams(all_placing_team_ids)


def get_tournament_standings():
    """Get complete tournament standings data formatted for API consumption."""
    varsity_speakers, novice_speakers = get_speaker_rankings()
    varsity_speakers = varsity_speakers[:10]
    novice_speakers = novice_speakers[:10]

    varsity_placements, varsity_placing_ids = get_team_placements_and_ids(Team.VARSITY)
    novice_placements, novice_placing_ids = get_team_placements_and_ids(Team.NOVICE)
    
    all_placing_team_ids = varsity_placing_ids | novice_placing_ids

    return {
        "varsity_speaker_awards": get_speaker_awards_data(varsity_speakers),
        "novice_speaker_awards": get_speaker_awards_data(novice_speakers),
        "varsity_team_placements": varsity_placements,
        "novice_team_placements": novice_placements,
        "non-placing_teams": get_nonplacing_teams(all_placing_team_ids),
        "new_debater_data": get_new_debater_data(),
        "new_schools_data": get_new_schools_data()
    }
