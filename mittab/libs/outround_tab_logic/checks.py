from mittab.apps.tab.models import *
from mittab.libs.errors import PrevRoundNotEnteredError


def lost_teams(type_of_round=None):
    """Get eliminated IDs without fetching either team's model instance."""
    outrounds = Outround.objects.all()
    if type_of_round is not None:
        outrounds = outrounds.filter(type_of_round=type_of_round)
    return [opp_id if victor in (Outround.GOV, Outround.GOV_VIA_FORFEIT) else gov_id
            for victor, gov_id, opp_id in outrounds.values_list(
                "victor", "gov_team_id", "opp_team_id")
            if victor in (Outround.GOV, Outround.GOV_VIA_FORFEIT,
                          Outround.OPP, Outround.OPP_VIA_FORFEIT)]


def have_enough_judges_type(type_of_round):
    loser_ids = lost_teams()

    panel_size = 3

    if type_of_round == BreakingTeam.VARSITY:
        panel_size = TabSettings.get("var_panel_size", 3)
    else:
        panel_size = TabSettings.get("nov_panel_size", 3)

    teams_count = BreakingTeam.objects.filter(
        type_of_team=type_of_round
    ).exclude(team__id__in=loser_ids).count()

    judges_needed = (
        teams_count // 2
    ) * panel_size

    num_judges = CheckIn.objects.filter(round_number=0).count()

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
    loser_ids = lost_teams()

    num_teams = BreakingTeam.objects.filter(
        type_of_team=type_of_round
    ).exclude(team__id__in=loser_ids).count()

    rooms_needed = (
        num_teams // 2
    )

    num_rooms = RoomCheckIn.objects.filter(round_number=0).count()

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


def get_break_sizes():
    # Defaults mirror settings/tab_policy.yaml. Tournaments that never saved
    # these settings have no TabSettings rows for them.
    return (TabSettings.get("var_teams_to_break", 8),
            TabSettings.get("nov_teams_to_break", 4))


def have_enough_rooms_before_break():
    var_breaking, nov_breaking = get_break_sizes()

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
