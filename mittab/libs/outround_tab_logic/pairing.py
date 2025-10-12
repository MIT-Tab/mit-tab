import math
import random

from mittab.apps.tab.models import *

from mittab.libs.outround_tab_logic.checks import have_enough_rooms
from mittab.libs.outround_tab_logic.bracket_generation import gen_bracket
from mittab.libs.outround_tab_logic.helpers import get_concurrent_round_size
from mittab.libs.tab_logic import (
    have_properly_entered_data,
    add_scratches_for_school_affil
)
from mittab.libs.tab_logic.stats import num_govs
from mittab.libs import errors
import mittab.libs.cache_logic as cache_logic

from mittab.apps.tab.team_views import get_team_rankings


def perform_the_break():
    teams, nov_teams = cache_logic.cache_fxn_key(
        get_team_rankings,
        "team_rankings",
        cache_logic.DEFAULT,
        None
    )

    nov_teams_to_break = TabSettings.get("nov_teams_to_break", 0)
    var_teams_to_break = TabSettings.get("var_teams_to_break", 0)

    if not var_teams_to_break:
        return False, "Please check your break tab settings"

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
    if BreakingTeam.objects.filter(
            type_of_team=BreakingTeam.NOVICE
    ).count() >= 2:
        pair(BreakingTeam.NOVICE)
    else:
        Outround.objects.filter(type_of_round=BreakingTeam.NOVICE).delete()
        TabSettings.set("nov_outrounds_public", 0)

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

    other_round_num = get_concurrent_round_size(num_teams, type_of_break)

    if other_round_num:
        other_queryset = Outround.objects.filter(
            type_of_round=not type_of_break,
            num_teams=other_round_num
        )
    else:
        other_queryset = Outround.objects.none()

    rooms = [r.room
             for r in RoomCheckIn.objects.filter(round_number=0)
             .prefetch_related("room")]
    rooms.sort(key=lambda r: r.rank, reverse=True)

    for room in rooms:
        if not base_queryset.filter(room=room).exists() and \
           not other_queryset.filter(room=room).exists():
            return room
    return None


def gov_team(team_one, team_two):
    """
    Determine which team should be gov in an outround pairing.

    Priority order:
    1. Sidelock - if teams have debated before, they must be on opposite sides
    2. Least govs - team with fewer gov rounds gets gov
    3. Random - if gov counts are equal

    Returns:
        (sidelock: bool, gov_team: BreakingTeam)
    """
    sidelock = TabSettings.get("sidelock", 0)

    # 1. Check for sidelock
    if sidelock:
        if Round.objects.filter(gov_team=team_one.team,
                                opp_team=team_two.team).exists():
            return True, team_two
        elif Round.objects.filter(gov_team=team_two.team,
                                  opp_team=team_one.team).exists():
            return True, team_one

    # 2. Check for least govs
    team_one_govs = num_govs(team_one.team)
    team_two_govs = num_govs(team_two.team)

    if team_one_govs < team_two_govs:
        return False, team_one
    elif team_two_govs < team_one_govs:
        return False, team_two

    # 3. Random assignment (govs are equal)
    if random.randint(0, 1) == 0:
        return False, team_one
    else:
        return False, team_two


def pair(type_of_break=BreakingTeam.VARSITY):
    add_scratches_for_school_affil()

    lost_outround = [t.loser.id for t in Outround.objects.all() if t.loser]

    base_queryset = BreakingTeam.objects.filter(
        type_of_team=type_of_break
    ).exclude(
        team__id__in=lost_outround
    )

    num_teams = base_queryset.count()

    if num_teams == 0:
        return

    teams_for_bracket = num_teams

    if num_teams < 2:
        Outround.objects.filter(type_of_round=type_of_break).delete()
        if type_of_break == BreakingTeam.VARSITY:
            TabSettings.set("var_outrounds_public", 0)
        else:
            TabSettings.set("nov_outrounds_public", 0)
        return

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
        team_one = base_queryset.filter(
            effective_seed=pairing[0]).prefetch_related(
                "team__gov_team",
                "team__gov_team_outround").first()
        team_two = base_queryset.filter(
            effective_seed=pairing[1]).prefetch_related(
                "team__gov_team",
                "team__gov_team_outround").first()

        if not team_one or not team_two:
            continue

        sidelock, gov = gov_team(team_one, team_two)
        opp = team_one if gov == team_two else team_two

        Outround.objects.create(
            num_teams=num_teams,
            type_of_round=type_of_break,
            gov_team=gov.team,
            opp_team=opp.team,
            room=get_next_available_room(num_teams,
                                         type_of_break=type_of_break),
            sidelock=sidelock
        )
