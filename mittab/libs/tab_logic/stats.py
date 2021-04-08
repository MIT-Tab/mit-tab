from collections import defaultdict
from django.db.models import Q

from mittab.apps.tab.models import Round, Bye, NoShow, TabSettings
from mittab.libs.cache_logic import cache

MAXIMUM_DEBATER_RANKS = 3.5
MINIMUM_DEBATER_SPEAKS = 0.0

##############################
# General team calculations: #
##############################


def num_byes(team):
    return Bye.objects.filter(bye_team=team).count()


def num_forfeit_wins(team):
    return Round.objects.filter(
        Q(gov_team=team, victor=Round.GOV_VIA_FORFEIT)
        | Q(opp_team=team, victor=Round.OPP_VIA_FORFEIT)
        | Q(gov_team=team, victor=Round.ALL_WIN)
        | Q(opp_team=team, victor=Round.ALL_WIN)).count()


def won_by_forfeit(round_obj, team):
    if round_obj.opp_team != team and round_obj.gov_team != team:
        return False
    elif round_obj.victor == Round.ALL_WIN:
        return True
    elif round_obj.victor == Round.GOV_VIA_FORFEIT:
        return round_obj.gov_team == team
    elif round_obj.victor == Round.OPP_VIA_FORFEIT:
        return round_obj.opp_team == team
    return False


def forfeited_round(round_obj, team):
    if round_obj.opp_team != team and round_obj.gov_team != team:
        return False
    elif round_obj.victor == Round.GOV_VIA_FORFEIT:
        return round_obj.opp_team == team
    elif round_obj.victor == Round.OPP_VIA_FORFEIT:
        return round_obj.gov_team == team
    return False


def hit_pull_up(team):
    return Round.objects.filter(gov_team=team, pullup=Round.OPP).exists() or \
            Round.objects.filter(opp_team=team, pullup=Round.GOV).exists()


def pull_up_count(team):
    return Round.objects.filter(gov_team=team, pullup=Round.GOV).exists() + \
            Round.objects.filter(opp_team=team, pullup=Round.OPP).exists()


def num_opps(team):
    return Round.objects.filter(opp_team=team).count()


def num_govs(team):
    return Round.objects.filter(gov_team=team).count()


def had_bye(team, round_number=None):
    if round_number is None:
        return Bye.objects.filter(bye_team=team).exists()
    else:
        return Bye.objects.filter(bye_team=team,
                                  round_number=round_number).exists()


##############
# Team wins: #
##############


@cache()
def tot_wins(team):
    normal_wins = Round.objects.filter(
        Q(gov_team=team, victor=Round.GOV)
        | Q(opp_team=team, victor=Round.OPP)).count()
    return normal_wins + num_byes(team) + num_forfeit_wins(team)


################
# Team speaks: #
################


@cache()
def tot_speaks(team):
    return sum([tot_speaks_deb(deb, False) for deb in team.debaters.all()])


@cache()
def single_adjusted_speaks(team):
    speaks = [speaks_for_debater(deb, False) for deb in team.debaters.all()]
    speaks = sorted([item for sublist in speaks for item in sublist])
    return sum(speaks[1:-1])


@cache()
def double_adjusted_speaks(team):
    speaks = [speaks_for_debater(deb, False) for deb in team.debaters.all()]
    speaks = sorted([item for sublist in speaks for item in sublist])
    return sum(speaks[2:-2])


###############
# Team ranks: #
###############


@cache()
def tot_ranks(team):
    return sum([tot_ranks_deb(deb, False) for deb in team.debaters.all()])


@cache()
def single_adjusted_ranks(team):
    ranks = [ranks_for_debater(deb, False) for deb in team.debaters.all()]
    ranks = sorted([item for sublist in ranks for item in sublist])
    return sum(ranks[1:-1])


@cache()
def double_adjusted_ranks(team):
    ranks = [ranks_for_debater(deb, False) for deb in team.debaters.all()]
    ranks = sorted([item for sublist in ranks for item in sublist])
    return sum(ranks[2:-2])


@cache()
def opp_strength(team):
    """
    Average number of wins per opponent

    Tracks opp strength while minimizing the effect that byes have on a team's
    opp strength
    """
    opponent_count = 0
    opponent_wins = 0

    gov_rounds = Round.objects.filter(gov_team=team)
    opp_rounds = Round.objects.filter(opp_team=team)

    for round_obj in gov_rounds:
        opponent_wins += tot_wins(round_obj.opp_team)
        opponent_count += 1
    for round_obj in opp_rounds:
        opponent_wins += tot_wins(round_obj.gov_team)
        opponent_count += 1

    if opponent_count > 0:
        return float(opponent_wins) / float(opponent_count)
    else:
        return 0.0


###################
# Debater speaks: #
###################


@cache()
def avg_deb_speaks(debater):
    """ Computes the average debater speaks for the supplied debater

    Generally this consistes of finding all the speaks we have, averaging iron
    men speaks, and then dividing by the length.

    This does not count forfeit losses or noshow's as 0 because lacking those
    speaker points in the first place is penalty enough, and some tab policies
    may want forfeits to count as average speaks.
    """
    real_speaks = []
    num_speaks = TabSettings.get("cur_round") - 1
    debater_roundstats = debater.roundstats_set.all()
    team = debater.team

    speaks_per_round = defaultdict(list)
    # We might have multiple roundstats per round if we have iron men, so first
    # group by round number
    for roundstat in debater_roundstats:
        speaks_per_round[roundstat.round.round_number].append(roundstat)

    for round_number in range(1, num_speaks + 1):
        roundstats = speaks_per_round[round_number]
        if roundstats:
            speaks = [float(rs.speaks) for rs in roundstats]
            avg_speaks = sum(speaks) / float(len(roundstats))
            roundstat = roundstats[0]
            if (won_by_forfeit(roundstat.round, team)
                    or forfeited_round(roundstat.round, team)):
                continue
            real_speaks.append(avg_speaks)

    if not real_speaks:
        return 0
    else:
        return float(sum(real_speaks)) / float(len(real_speaks))


def debater_forfeit_speaks(_debater):
    """ Calculate a debater's speaks for a forfeit round

    Note that right now we just return 0, but we may want to add support
    for returning average speaks or some such
    """
    return 0.0


@cache()
def speaks_for_debater(debater, average_ironmen=True):
    """Returns a list of speaks for the provided debater

    In most normal rounds the speaks of the debater are the speaks the judge
    gave them, but there are some special circumstances.

    Forfeits:
    If a debater won by forfeit, they get their average speaks
    If a debater lost by forfeit, they get speaks of 0
    If a debater was Noshow for a round, they get speaks of 0

    Iron Mans:
    If a debater is an iron man for a round, they get the average of their
    speaks for that round

    Byes:
    If a debater wins in a bye, they get their average speaks
    If a debater was late to a lenient round, they get average speaks
    """
    team = debater.team
    # We start counting at 1, so when cur_round says 6 that means that we are
    # in round 5 and should have 5 speaks
    num_speaks = TabSettings.get("cur_round") - 1

    debater_roundstats = debater.roundstats_set.all()
    debater_speaks = []

    speaks_per_round = defaultdict(list)
    # We might have multiple roundstats per round if we have iron men, so first
    # group by round number
    for roundstat in debater_roundstats:
        speaks_per_round[roundstat.round.round_number].append(roundstat)

    for round_number in range(1, num_speaks + 1):
        roundstats = speaks_per_round[round_number]
        if roundstats:
            # This is so if in the odd chance we get a debater paired in
            # twice we take the speaks they actually got
            roundstats.sort(key=lambda rs: rs.speaks, reverse=True)
            roundstat = roundstats[0]
            if not len(set(rs.round for rs in roundstats)) == 1:
                roundstats = roundstats[:1]

            speaks = [float(rs.speaks) for rs in roundstats]
            avg_speaks = sum(speaks) / float(len(roundstats))
            if won_by_forfeit(roundstat.round, team):
                debater_speaks.append(avg_deb_speaks(debater))
            elif forfeited_round(roundstat.round, team):
                debater_speaks.append(MINIMUM_DEBATER_SPEAKS)
            else:
                if average_ironmen:
                    debater_speaks.append(avg_speaks)
                else:
                    debater_speaks.extend(speaks)
        else:
            speaks = debater_abnormal_round_speaks(debater, round_number)
            if speaks is not None:
                debater_speaks.append(speaks)

    debater_speaks = list(map(float, debater_speaks))
    return debater_speaks


def debater_abnormal_round_speaks(debater, round_number):
    """
    Calculate the ranks for a bye/forfeit round

    Forfeits:
    If the round is set to `lenient_late`, it uses average speaks
    Otherwise, it uses speaks of 0.0

    Byes:
    Uses average speaks
    """
    team = debater.team
    had_noshow = NoShow.objects.filter(round_number=round_number,
                                       no_show_team=team)
    if had_bye(team, round_number) or (had_noshow
                                       and had_noshow.first().lenient_late):
        return avg_deb_speaks(debater)
    elif had_noshow:
        return MINIMUM_DEBATER_SPEAKS


def single_adjusted_speaks_deb(debater):
    debater_speaks = speaks_for_debater(debater)
    debater_speaks.sort()
    return sum(debater_speaks[1:-1])


def double_adjusted_speaks_deb(debater):
    debater_speaks = speaks_for_debater(debater)
    debater_speaks.sort()
    return sum(debater_speaks[2:-2])


@cache()
def tot_speaks_deb(debater, average_ironmen=True):
    """Return the total of all speaks for a debater"""
    debater_speaks = speaks_for_debater(debater, average_ironmen)
    return sum(debater_speaks)


##################
# Debater ranks: #
##################


@cache()
def avg_deb_ranks(debater):
    """ Computes the average debater ranks for the supplied debater

    Generally this consistes of finding all the ranks we have, averaging iron
    men ranks, and then dividing by the length.

    This does not count forfeit losses or noshow's as 7 because having ranks of
    3.5 in the first place is penalty enough, and some tab polices may want
    forfeits to count as average ranks.
    """
    real_ranks = []
    num_ranks = TabSettings.get("cur_round") - 1
    debater_roundstats = debater.roundstats_set.all()
    team = debater.team

    ranks_per_round = defaultdict(list)
    # We might have multiple roundstats per round if we have iron men, so first
    # group by round number
    for roundstat in debater_roundstats:
        ranks_per_round[roundstat.round.round_number].append(roundstat)

    for round_number in range(1, num_ranks + 1):
        roundstats = ranks_per_round[round_number]
        if roundstats:
            ranks = [float(rs.ranks) for rs in roundstats]
            avg_ranks = sum(ranks) / float(len(roundstats))
            roundstat = roundstats[0]
            if (won_by_forfeit(roundstat.round, team)
                    or forfeited_round(roundstat.round, team)):
                continue
            real_ranks.append(avg_ranks)

    if not real_ranks:
        return 0
    else:
        return float(sum(real_ranks)) / float(len(real_ranks))


@cache()
def ranks_for_debater(debater, average_ironmen=True):
    """Returns a list of ranks for the provided debater

    In most normal rounds the ranks of the debater are the ranks the judge
    gave them, but there are some special circumstances.

    Forfeits:
    If a debater won by forfeit, they get their average ranks
    If a debater lost by forfeit, they get ranks of 0
    If a debater was Noshow for a round, they get ranks of 0

    Iron Mans:
    If a debater is an iron man for a round, they get the average of their
    ranks for that round

    Byes:
    If a debater wins in a bye, they get their average ranks
    If a debater was late to a lenient round, they get average ranks
    """
    team = debater.team
    # We start counting at 1, so when cur_round says 6 that means that we are
    # in round 5 and should have 5 ranks
    num_ranks = TabSettings.get("cur_round") - 1

    debater_roundstats = debater.roundstats_set.all()
    debater_ranks = []

    ranks_per_round = defaultdict(list)
    # We might have multiple roundstats per round if we have iron men, so first
    # group by round number
    for roundstat in debater_roundstats:
        ranks_per_round[roundstat.round.round_number].append(roundstat)

    for round_number in range(1, num_ranks + 1):
        roundstats = ranks_per_round[round_number]
        if roundstats:
            ranks = [float(rs.ranks) for rs in roundstats]
            avg_ranks = sum(ranks) / float(len(roundstats))
            roundstat = roundstats[0]
            if won_by_forfeit(roundstat.round, team):
                debater_ranks.append(avg_deb_ranks(debater))
            elif forfeited_round(roundstat.round, team):
                debater_ranks.append(MAXIMUM_DEBATER_RANKS)
            else:
                if average_ironmen:
                    debater_ranks.append(avg_ranks)
                else:
                    debater_ranks.extend(ranks)
        else:
            ranks = debater_abnormal_round_ranks(debater, round_number)
            if ranks is not None:
                debater_ranks.append(ranks)

    debater_ranks = list(map(float, debater_ranks))
    return debater_ranks


@cache()
def debater_abnormal_round_ranks(debater, round_number):
    """
    Calculate the ranks for a bye/forfeit round

    Forfeits:
    If the round is set to `lenient_late`, it uses average ranks
    Otherwise, it uses ranks of 3.5

    Byes:
    Uses average ranks
    """
    team = debater.team
    had_noshow = NoShow.objects.filter(round_number=round_number,
                                       no_show_team=team)
    if had_bye(team, round_number) or (had_noshow
                                       and had_noshow.first().lenient_late):
        return avg_deb_ranks(debater)
    elif had_noshow:
        return MAXIMUM_DEBATER_RANKS


@cache()
def single_adjusted_ranks_deb(debater):
    debater_ranks = ranks_for_debater(debater)
    debater_ranks.sort()
    return sum(debater_ranks[1:-1])


@cache()
def double_adjusted_ranks_deb(debater):
    debater_ranks = ranks_for_debater(debater)
    debater_ranks.sort()
    return sum(debater_ranks[2:-2])


@cache()
def tot_ranks_deb(debater, average_ironmen=True):
    debater_ranks = ranks_for_debater(debater, average_ironmen=average_ironmen)
    return sum(debater_ranks)
