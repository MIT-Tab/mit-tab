from abc import ABC
from functools import total_ordering

from mittab.apps.tab.models import Debater, Team
from mittab.libs.tab_logic.stats import *


def rank_speakers():
    return sorted([
        DebaterScore(d)
        for d in Debater.objects.prefetch_related("team_set").all()
    ])


def rank_teams():
    return sorted([
        TeamScore(d) for d in Team.objects.prefetch_related("debaters").all()
    ])


class __stat:
    def __init__(self, name, sort_coefficient=1):
        self.name = name
        self.sort_coefficient = sort_coefficient


WINS = __stat("Wins", -1)
SPEAKS = __stat("Speaks", -1)
RANKS = __stat("Ranks")
SINGLE_ADJUSTED_SPEAKS = __stat("Single adjusted speaks", -1)
SINGLE_ADJUSTED_RANKS = __stat("Single adjusted ranks")
DOUBLE_ADJUSTED_SPEAKS = __stat("Double adjusted speaks", -1)
DOUBLE_ADJUSTED_RANKS = __stat("Double adjusted ranks")
OPP_STRENGTH = __stat("Opp strength", -1)
COIN_FLIP = __stat("Coin flip")


@total_ordering
class Score(ABC):
    def __eq__(self, other):
        return self.scoring_tuple() == other.scoring_tuple()

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        return self.scoring_tuple() < other.scoring_tuple()

    def scoring_tuple(self):
        return tuple(
            map(lambda stat: stat.sort_coefficient * self.stats[stat],
                self.stat_priority))

    def get_tiebreaker(self, other):
        for stat, self_val, other_val in zip(self.stat_priority,
                                             self.scoring_tuple(),
                                             other.scoring_tuple()):
            if self_val != other_val: return stat
        return None

    def __getitem__(self, key):
        return self.stats[key]


class DebaterScore(Score):
    stat_priority = (SPEAKS, RANKS, SINGLE_ADJUSTED_SPEAKS,
                     SINGLE_ADJUSTED_RANKS, DOUBLE_ADJUSTED_SPEAKS,
                     DOUBLE_ADJUSTED_RANKS, COIN_FLIP)

    def __init__(self, debater):
        self.debater = debater
        self.stats = {}
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
        self.team = team
        self.stats = {}
        self.stats[WINS] = tot_wins(team)
        self.stats[SPEAKS] = tot_speaks(team)
        self.stats[RANKS] = tot_ranks(team)
        self.stats[SINGLE_ADJUSTED_SPEAKS] = single_adjusted_speaks(team)
        self.stats[SINGLE_ADJUSTED_RANKS] = single_adjusted_ranks(team)
        self.stats[DOUBLE_ADJUSTED_SPEAKS] = double_adjusted_speaks(team)
        self.stats[DOUBLE_ADJUSTED_RANKS] = double_adjusted_ranks(team)
        self.stats[OPP_STRENGTH] = opp_strength(team)
        self.stats[COIN_FLIP] = team.tiebreaker
