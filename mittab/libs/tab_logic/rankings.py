from functools import total_ordering

from mittab.apps.tab.models import Debater
from mittab.libs.tab_logic.stats import *

def rank_speakers():
    return sorted([ DebaterScore(d) for d in Debater.objects.prefetch_related('team_set').all() ])

def rank_teams():
    return sorted([ TeamScore(d) for d in Team.objects.prefetch_related('debaters').all() ])

@total_ordering
class DebaterScore(object):
    def __init__(self, debater):
        self.debater = debater
        self.speaks = tot_speaks_deb(debater)
        self.ranks = tot_ranks_deb(debater)
        self.single_adjusted_speaks = single_adjusted_speaks_deb(debater)
        self.single_adjusted_ranks = single_adjusted_ranks_deb(debater)
        self.double_adjusted_speaks = double_adjusted_speaks_deb(debater)
        self.double_adjusted_ranks = double_adjusted_ranks_deb(debater)

    def __eq__(self, other):
        return self._scoring_tuple() == other._scoring_tuple()

    def __ne__(self, other):
        return not (self == other)

    def __lt__(self, other):
        return self._scoring_tuple() < other._scoring_tuple()

    def _scoring_tuple(self):
        return (-self.speaks,
                self.ranks,
                -self.single_adjusted_speaks,
                self.single_adjusted_ranks,
                -self.double_adjusted_speaks,
                self.double_adjusted_ranks)

@total_ordering
class TeamScore(object):
    def __init__(self, team):
        self.team = team
        self.wins = tot_wins(team)
        self.speaks = tot_speaks(team)
        self.ranks = tot_ranks(team)
        self.single_adjusted_speaks = single_adjusted_speaks(team)
        self.single_adjusted_ranks = single_adjusted_ranks(team)
        self.double_adjusted_speaks = double_adjusted_speaks(team)
        self.double_adjusted_ranks = double_adjusted_ranks(team)
        self.opp_strength = opp_strength(team)

    def __eq__(self, other):
        return self._scoring_tuple() == other._scoring_tuple()

    def __ne__(self, other):
        return not (self == other)

    def __lt__(self, other):
        return self._scoring_tuple() < other._scoring_tuple()

    def _scoring_tuple(self):
        return (-self.wins,
                -self.speaks,
                self.ranks,
                -self.single_adjusted_speaks,
                self.single_adjusted_ranks,
                -self.double_adjusted_speaks,
                self.double_adjusted_ranks,
                -self.opp_strength)
