import json

from mittab.apps.tab.models import *
from mittab.libs import tab_logic


def get_num_rounds_debated(team):
    num_rounds = team.gov_team.exclude(
        victor=Round.OPP_VIA_FORFEIT
    ).exclude(
        victor=Round.ALL_DROP
    ).count()

    num_rounds += team.opp_team.exclude(
        victor=Round.GOV_VIA_FORFEIT
    ).exclude(
        victor=Round.ALL_DROP
    ).count()

    num_rounds += Bye.objects.filter(bye_team=team).count()

    return num_rounds


def generate_json_dump():
    schools = [{
        'id': school.id,
        'name': school.name
    } for school in School.objects.all()]

    teams = [{
        'id': team.id,
        'debaters': [{
            'id': debater.id,
            'name': debater.name,
            'status': debater.novice_status,
        } for debater in team.debaters.all()],
        'school_id': team.school.id,
        'hybrid_school_id': team.hybrid_school.id if team.hybrid_school else -1,
        'num_rounds': get_num_rounds_debated(team),
    } for team in Team.objects.all()]

    judges = [{
        'id': judge.id,
        'name': judge.name,
        'schools': [school.id for school in judge.schools.all()]
    } for judge in Judge.objects.all()]

    rounds = [{
        'id': round.id,
        'round_number': round.round_number,
        'gov': round.gov_team.id,
        'opp': round.opp_team.id,
        'judges': [judge.id for judge in round.judges.all()],
        'victor': round.victor
    } for round in Round.objects.all()]

    byes = [{
        'team': bye.bye_team.id,
        'round_number': bye.round_number
    } for bye in Bye.objects.all()]

    no_shows = [{
        'team': no_show.no_show_team.id,
        'round_number': no_show.round_number
    } for no_show in NoShow.objects.all()]

    stats = [{
        'debater': stat.debater.id,
        'round': stat.round.id,
        'speaks': stat.speaks,
        'ranks': stat.ranks,
        'role': stat.debater_role
    } for stat in RoundStats.objects.all()]

    ranked_teams = tab_logic.rankings.rank_teams()
    ranked_speakers = tab_logic.rank_speakers()

    won_outrounds = [t.winner.id for t in Outround.objects.all() if t.winner]

    speaker_rankings = []
    team_rankings = []

    novice_speaker_rankings = []
    novice_team_rankings = []

    place = 1
    for ranking in ranked_speakers:
        speaker_rankings += [{
            'debater': ranking.debater.id,
            'place': place
        }]

        place += 1

    place = 1

    ranked_speakers = list(filter(lambda ranking: ranking.debater.novice_status == Debater.NOVICE,
                                  ranked_speakers))

    for ranking in ranked_speakers:
        novice_speaker_rankings += [{
            'debater': ranking.debater.id,
            'place': place
        }]

        place += 1

    place = 1
    for ranking in ranked_teams:
        team_rankings += [{
            'team': ranking.team.id,
            'place': place,
            'won_outrounds': 32 - won_outrounds.count(ranking.team.id)
        }]

        place += 1

    team_rankings.sort(key=lambda ranking: (ranking['won_outrounds'], ranking['place']))

    place = 1
    for r in team_rankings:
        r['place'] = place
        place += 1

    ranked_teams = list(filter(
        lambda ts: all(
            map(lambda debater: debater.novice_status == Debater.NOVICE, ts.team.debaters.all())), ranked_teams))

    place = 1
    for ranking in ranked_teams:
        novice_team_rankings += [{
            'team': ranking.team.id,
            'place': place,
            'won_outrounds': 32 - won_outrounds.count(ranking.team.id)
        }]

        place += 1

    novice_team_rankings.sort(key=lambda ranking: (ranking['won_outrounds'], ranking['place']))

    place = 1
    for r in novice_team_rankings:
        r['place'] = place
        place += 1

    to_return = {}

    to_return['schools'] = schools
    to_return['teams'] = teams
    to_return['judges'] = judges
    to_return['rounds'] = rounds
    to_return['byes'] = byes
    to_return['no_shows'] = no_shows
    to_return['stats'] = stats

    to_return['speaker_results'] = speaker_rankings
    to_return['novice_speaker_results'] = novice_speaker_rankings
    to_return['team_results'] = team_rankings if Outround.objects.count() > 0 else []
    to_return['novice_team_results'] = novice_team_rankings if Outround.objects.count() > 0 else []

    to_return['num_rounds'] = TabSettings.get('tot_rounds')

    return to_return
