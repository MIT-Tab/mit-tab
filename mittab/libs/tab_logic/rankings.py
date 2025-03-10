from abc import ABC
from functools import total_ordering

from mittab.apps.tab.models import Debater, Team
from mittab.libs.tab_logic.stats import *


def rank_speakers():
    debaters = Debater.objects.prefetch_related(
        "team_set",
        "team_set__byes",
        "team_set__no_shows",
        "roundstats_set",
        "roundstats_set__round",
    ).all()
    return sorted([
        DebaterScore(d)
        for d in debaters
    ])


def rank_teams():
    all_teams = Team.objects.all().prefetch_related(
        "gov_team",  # poorly named relation, gets rounds as gov team
        "opp_team",  # poorly named relation, rounds as opp team
        # for all gov rounds, load the opp team's gov+opp rounds (opp-strength)
        "gov_team__opp_team__gov_team",
        "gov_team__opp_team__opp_team",
        "gov_team__opp_team__byes",
        # for all opp rounds, load the gov team's gov+opp rounds (opp-strength)
        "opp_team__gov_team__gov_team",
        "opp_team__gov_team__opp_team",
        "opp_team__gov_team__byes",
        "byes",
        "no_shows",
        "debaters",
        "debaters__roundstats_set",
        "debaters__roundstats_set__round",
        "debaters__team_set",
        "debaters__team_set__no_shows",
        "debaters__team_set__byes",
    )
    return sorted(TeamScore(d) for d in all_teams)


class Stat:
    def __init__(self, name, sort_coefficient=1):
        self.name = name
        self.sort_coefficient = sort_coefficient


WINS = Stat("Wins", -1)
SPEAKS = Stat("Speaks", -1)
RANKS = Stat("Ranks")
SINGLE_ADJUSTED_SPEAKS = Stat("Single adjusted speaks", -1)
SINGLE_ADJUSTED_RANKS = Stat("Single adjusted ranks")
DOUBLE_ADJUSTED_SPEAKS = Stat("Double adjusted speaks", -1)
DOUBLE_ADJUSTED_RANKS = Stat("Double adjusted ranks")
OPP_STRENGTH = Stat("Opp strength", -1)
COIN_FLIP = Stat("Coin flip")


@total_ordering
class Score(ABC):
    stat_priority = ()

    def __init__(self):
        self.stats = {}

    def __eq__(self, other):
        return self.scoring_tuple() == other.scoring_tuple()

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        return self.scoring_tuple() < other.scoring_tuple()

    def scoring_tuple(self):
        return tuple(
            map(lambda stat: stat.sort_coefficient * self[stat],
                self.stat_priority))

    def get_tiebreaker(self, other):
        for stat, self_val, other_val in zip(self.stat_priority,
                                             self.scoring_tuple(),
                                             other.scoring_tuple()):
            if self_val != other_val:
                return stat
        return None

    def __getitem__(self, key):
        return self.stats[key] or 0


class DebaterScore(Score):
    stat_priority = (SPEAKS, RANKS, SINGLE_ADJUSTED_SPEAKS,
                     SINGLE_ADJUSTED_RANKS, DOUBLE_ADJUSTED_SPEAKS,
                     DOUBLE_ADJUSTED_RANKS, COIN_FLIP)

    def __init__(self, debater):
        super(DebaterScore, self).__init__()
        self.debater = debater
        self.stats[SPEAKS] = tot_speaks_deb(debater)
        self.stats[RANKS] = tot_ranks_deb(debater)
        self.stats[SINGLE_ADJUSTED_SPEAKS] = single_adjusted_speaks_deb(
            debater)
        self.stats[SINGLE_ADJUSTED_RANKS] = single_adjusted_ranks_deb(debater)
        self.stats[DOUBLE_ADJUSTED_SPEAKS] = double_adjusted_speaks_deb(
            debater)
        self.stats[DOUBLE_ADJUSTED_RANKS] = double_adjusted_ranks_deb(debater)
        self.stats[COIN_FLIP] = debater.tiebreaker


@total_ordering
class TeamScore(Score):
    stat_priority = (WINS, SPEAKS, RANKS, SINGLE_ADJUSTED_SPEAKS,
                     SINGLE_ADJUSTED_RANKS, DOUBLE_ADJUSTED_SPEAKS,
                     DOUBLE_ADJUSTED_RANKS, OPP_STRENGTH, COIN_FLIP)

    def __init__(self, team):
        super(TeamScore, self).__init__()
        self.team = team
        self.stats[WINS] = tot_wins(team)
        self.stats[SPEAKS] = tot_speaks(team)
        self.stats[RANKS] = tot_ranks(team)
        self.stats[SINGLE_ADJUSTED_SPEAKS] = single_adjusted_speaks(team)
        self.stats[SINGLE_ADJUSTED_RANKS] = single_adjusted_ranks(team)
        self.stats[DOUBLE_ADJUSTED_SPEAKS] = double_adjusted_speaks(team)
        self.stats[DOUBLE_ADJUSTED_RANKS] = double_adjusted_ranks(team)
        self.stats[OPP_STRENGTH] = opp_strength(team)
        self.stats[COIN_FLIP] = team.tiebreaker
