from mittab.apps.tab.models import *
from mittab.libs.errors import PrevRoundNotEnteredError


def lost_teams():
    losers = [outround.loser.id
              for outround in Outround.objects.all()
              if outround.loser]

    return losers


def have_enough_judges_type(type_of_round):
    lost_ids = [outround.loser.id
                for outround in Outround.objects.all()
                if outround.loser]

    panel_size = 3

    if type_of_round == BreakingTeam.VARSITY:
        panel_size = TabSettings.get("var_panel_size", 3)
    else:
        panel_size = TabSettings.get("nov_panel_size", 3)

    teams_count = BreakingTeam.objects.filter(
        type_of_team=type_of_round
    ).exclude(team__id__in=lost_ids).count()

    judges_needed = (
        teams_count // 2
    ) * panel_size

    var_to_nov = TabSettings.get("var_to_nov", 2)

    num_teams = teams_count

    other_round_num = num_teams / var_to_nov
    if type_of_round == BreakingTeam.NOVICE:
        other_round_num = num_teams * var_to_nov

    other_round_type = not type_of_round

    num_in_use = Judge.objects.filter(
        judges_outrounds__num_teams=other_round_num,
        judges_outrounds__type_of_round=other_round_type
    ).count()

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


def have_enough_rooms_type(type_of_round):
    lost_ids = [outround.loser.id
                for outround in Outround.objects.all()
                if outround.loser]

    num_teams = BreakingTeam.objects.filter(
        type_of_team=type_of_round
    ).exclude(team__id__in=lost_ids).count()

    rooms_needed = (
        num_teams // 2
    )

    var_to_nov = TabSettings.get("var_to_nov", 2)

    other_round_num = num_teams / var_to_nov
    if type_of_round == BreakingTeam.NOVICE:
        other_round_num = num_teams * var_to_nov

    other_round_type = not type_of_round

    num_in_use = Room.objects.filter(
        rooms_outrounds__num_teams=other_round_num,
        rooms_outrounds__type_of_round=other_round_type
    ).count()

    num_rooms = Room.objects.filter(rank__gt=0).count() - num_in_use

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


def have_properly_entered_data(num_teams, type_of_round):
    outrounds = Outround.objects.filter(num_teams=num_teams,
                                        type_of_round=type_of_round,
                                        victor=0).exists()

    if outrounds:
        raise PrevRoundNotEnteredError()
