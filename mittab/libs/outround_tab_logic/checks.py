from mittab.apps.tab.models import *


def have_enough_judges():
    nov_panel_size = TabSettings.get("nov_panel_size", 3)
    var_panel_size = TabSettings.get("var_panel_size", 3)

    judges_needed = (
        BreakingTeam.objects.filter(
            type_of_team=BreakingTeam.VARSITY
        ).count() // 2
    ) * var_panel_size
    judges_needed += (
        BreakingTeam.objects.filter(
            type_of_team=BreakingTeam.NOVICE
        ).count() // 2
    ) * nov_panel_size
    num_judges = CheckIn.objects.filter(round_number=0).count()
    if num_judges < judges_needed:
        return False, (num_judges, judges_needed)
    return True, (num_judges, judges_needed)


def have_enough_rooms():
    future_rounds = BreakingTeam.objects.filter().count() // 2
    num_rooms = Room.objects.filter(rank__gt=0).count()
    if num_rooms < future_rounds:
        return False, (num_rooms, future_rounds)
    return True, (num_rooms, future_rounds)
