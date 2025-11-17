from mittab.apps.tab.models import *
from mittab.libs.errors import PrevRoundNotEnteredError

from mittab.libs.outround_tab_logic.helpers import get_concurrent_round


def lost_teams():
    return [outround.loser.id
            for outround in Outround.objects.all()
            if outround.loser]


def _bracket_size(teams_count):
    if teams_count < 2:
        return teams_count
    size = 1
    while size < teams_count:
        size <<= 1
    return size


def have_enough_judges_type(type_of_round, num_teams=None):
    loser_ids = [outround.loser.id
                 for outround in Outround.objects.all()
                 if outround.loser]

    panel_size = 3

    if type_of_round == BreakingTeam.VARSITY:
        panel_size = TabSettings.get("var_panel_size", 3)
    else:
        panel_size = TabSettings.get("nov_panel_size", 3)

    teams_qs = BreakingTeam.objects.filter(
        type_of_team=type_of_round
    ).exclude(team__id__in=loser_ids)
    teams_count = teams_qs.count()

    bracket_size = num_teams if num_teams is not None else _bracket_size(teams_count)

    judges_needed = (bracket_size // 2) * panel_size

    if bracket_size < 2:
        num_judges = CheckIn.objects.filter(round_number=0).count()
        return True, (num_judges, 0)

    concurrent_round = get_concurrent_round(type_of_round, bracket_size)

    num_in_use = 0
    if concurrent_round:
        other_round_type, other_round_num = concurrent_round
        num_in_use = Judge.objects.filter(
            judges_outrounds__num_teams=other_round_num,
            judges_outrounds__type_of_round=other_round_type
        ).distinct().count()

    num_judges = CheckIn.objects.filter(round_number=0).count() - num_in_use

    if num_judges < judges_needed:
        return False, (num_judges, judges_needed)
    return True, (num_judges, judges_needed)


def have_enough_judges():
    var = have_enough_judges_type(BreakingTeam.VARSITY)
    nov = have_enough_judges_type(BreakingTeam.NOVICE)

    return (
        var[0] and nov[0],
        (var[1][0], var[1][1] + nov[1][1])
    )


def have_enough_rooms_type(type_of_round, num_teams=None):
    loser_ids = [outround.loser.id
                 for outround in Outround.objects.all()
                 if outround.loser]

    teams_qs = BreakingTeam.objects.filter(
        type_of_team=type_of_round
    ).exclude(team__id__in=loser_ids)

    teams_count = teams_qs.count()

    bracket_size = num_teams if num_teams is not None else _bracket_size(teams_count)

    rooms_needed = bracket_size // 2

    if bracket_size < 2:
        num_rooms = RoomCheckIn.objects.filter(round_number=0).count()
        return True, (num_rooms, 0)

    concurrent_round = get_concurrent_round(type_of_round, bracket_size)

    num_in_use = 0
    if concurrent_round:
        other_round_type, other_round_num = concurrent_round
        num_in_use = Room.objects.filter(
            rooms_outrounds__num_teams=other_round_num,
            rooms_outrounds__type_of_round=other_round_type
        ).distinct().count()

    num_rooms = RoomCheckIn.objects.filter(round_number=0).count() - num_in_use

    if num_rooms < rooms_needed:
        return False, (num_rooms, rooms_needed)
    return True, (num_rooms, rooms_needed)


def have_enough_rooms():
    var = have_enough_rooms_type(BreakingTeam.VARSITY)
    nov = have_enough_rooms_type(BreakingTeam.NOVICE)

    return (
        var[0] and nov[0],
        (var[1][0], var[1][1] + nov[1][1])
    )


def have_enough_rooms_before_break():
    var_breaking = TabSettings.get("var_teams_to_break", 8)
    nov_breaking = TabSettings.get("nov_teams_to_break", 4)

    rooms_needed = var_breaking // 2
    rooms_needed += nov_breaking // 2

    num_rooms = RoomCheckIn.objects.filter(round_number=0).count()

    return (
        rooms_needed <= num_rooms, (num_rooms, rooms_needed)
    )


def have_properly_entered_data(num_teams, type_of_round):
    outrounds = Outround.objects.filter(num_teams=num_teams,
                                        type_of_round=type_of_round,
                                        victor=0).exists()

    if outrounds:
        raise PrevRoundNotEnteredError()
