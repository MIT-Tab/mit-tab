import math

from mittab.apps.tab.models import *

from mittab.libs.outround_tab_logic.checks import have_enough_rooms
from mittab.libs.outround_tab_logic.bracket_generation import gen_bracket
from mittab.libs.tab_logic import have_properly_entered_data
from mittab.libs import errors
import mittab.libs.cache_logic as cache_logic

from mittab.apps.tab.team_views import get_team_rankings


def perform_the_break():
    teams, nov_teams = cache_logic.cache_fxn_key(
        get_team_rankings,
        "team_rankings",
        None
    )

    nov_teams_to_break = TabSettings.get("nov_teams_to_break")
    var_teams_to_break = TabSettings.get("var_teams_to_break")

    if not nov_teams_to_break or not var_teams_to_break:
        return False, "Please check your break tab settings"

    # This forces a refresh of the breaking teams
    Outround.objects.all().delete()
    BreakingTeam.objects.all().delete()
    
    current_seed = 1
    for team in teams:
        if not team[0].break_preference == Team.VARSITY:
            continue

        if current_seed > var_teams_to_break:
            break

        BreakingTeam.objects.create(team=team[0],
                                    seed=current_seed,
                                    effective_seed=current_seed,
                                    type_of_team=BreakingTeam.VARSITY)
        current_seed += 1

    current_seed = 1
    for nov_team in nov_teams:
        if current_seed > nov_teams_to_break:
            break
        
        if BreakingTeam.objects.filter(team=nov_team[0]).exists():
            continue
        
        BreakingTeam.objects.create(team=nov_team[0],
                                    seed=current_seed,
                                    effective_seed=current_seed,
                                    type_of_team=BreakingTeam.NOVICE)

        current_seed += 1

    pair(BreakingTeam.VARSITY)
    pair(BreakingTeam.NOVICE)

    return True, "Success!"


def is_pairing_possible(num_teams):
    if num_teams > Team.objects.count():
        raise errors.NotEnoughTeamsError()

    if num_teams < 2:
        raise errors.NotEnoughTeamsError()

    # Check there are enough rooms
    if not have_enough_rooms()[0]:
        raise errors.NotEnoughRoomsError()

    # If we have results, they should be entered and there should be no
    # byes or noshows for teams that debated
    round_to_check = TabSettings.get("tot_rounds", 5)
    have_properly_entered_data(round_to_check)


def get_next_available_room(num_teams, type_of_break):
    base_queryset = Outround.objects.filter(num_teams=num_teams,
                                            type_of_round=type_of_break)

    var_to_nov = TabSettings.get("var_to_nov", 2)

    other_queryset = Outround.objects.filter(type_of_round=not type_of_break)

    if type_of_break == BreakingTeam.VARSITY:
        other_queryset = other_queryset.filter(num_teams=num_teams / var_to_nov)
    else:
        other_queryset = other_queryset.filter(num_teams=num_teams * var_to_nov)

    rooms = [r.room
             for r in RoomCheckIn.objects.filter(round_number=0) \
             .prefetch_related("room")]
    rooms.sort(key=lambda r: r.rank, reverse=True)

    for room in rooms:
        if not base_queryset.filter(room=room).exists() and \
           not other_queryset.filter(room=room).exists():
            return room
    return None


def gov_team(team_one, team_two):
    print(team_two)
    # PLACEHOLDER
    return team_one


def pair(type_of_break=BreakingTeam.VARSITY):
    lost_outround = [t.loser.id for t in Outround.objects.all() if t.loser]

    base_queryset = BreakingTeam.objects.filter(
        type_of_team=type_of_break
    ).exclude(
        team__id__in=lost_outround
    )

    num_teams = base_queryset.count()

    teams_for_bracket = num_teams

    while not math.log(teams_for_bracket, 2) % 1 == 0:
        teams_for_bracket += 1

    num_teams = teams_for_bracket

    is_pairing_possible(num_teams)

    Outround.objects.filter(
        num_teams=num_teams
    ).filter(
        type_of_round=type_of_break
    ).all().delete()

    if type_of_break == BreakingTeam.VARSITY:
        TabSettings.set("var_outrounds_public", 0)
    else:
        TabSettings.set("nov_outrounds_public", 0)

    bracket = gen_bracket(num_teams)

    for pairing in bracket:
        team_one = base_queryset.filter(effective_seed=pairing[0]).first()
        team_two = base_queryset.filter(effective_seed=pairing[1]).first()

        print(pairing)

        if not team_one or not team_two:
            continue

        gov = gov_team(team_one, team_two)
        opp = team_one if gov == team_two else team_two

        Outround.objects.create(
            num_teams=num_teams,
            type_of_round=type_of_break,
            gov_team=gov.team,
            opp_team=opp.team,
            room=get_next_available_room(num_teams,
                                         type_of_break=type_of_break)
        )
