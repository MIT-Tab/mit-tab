class DebaterScores(object):
    def __init__(self, s, t):
        # (-tot_speaks_deb(debater),
        #  tot_ranks_deb(debater),
        #  -single_adjusted_speaks_deb(debater),
        #  single_adjusted_ranks_deb(debater),
        #  -double_adjusted_speaks_deb(debater),
        #  double_adjusted_ranks_deb(debater))
        self.speaker = s
        self.tot_speaks = -t[0]
        self.tot_ranks = t[1]
        self.s_adj_speaks = -t[2]
        self.s_adj_ranks = t[3]
        self.d_adj_speaks = -t[4]
        self.d_adj_ranks = t[5]

    def create_scoring_tuple(self):
        return (
            -self.tot_speaks,
            self.tot_ranks,
            -self.s_adj_speaks,
            self.s_adj_ranks,
            -self.d_adj_speaks,
            self.d_adj_ranks
        )


class TeamScores(object):
    def __init__(self, team, data):
        # (-tot_wins(team),
        #  -tot_speaks(team),
        #  tot_ranks(team),
        #  -single_adjusted_speaks(team),
        #  single_adjusted_ranks(team),
        #  -double_adjusted_speaks(team),
        #  double_adjusted_ranks(team),
        #  -opp_strength(team))
        self.team = team
        self.wins = -data[0]
        self.tot_speaks = -data[1]
        self.tot_ranks = data[2]
        self.s_adj_speaks = -data[3]
        self.s_adj_ranks = data[4]
        self.d_adj_speaks = -data[5]
        self.d_adj_ranks = data[6]

    def create_scoring_tuple(self):
        return (-self.wins,
                -self.tot_speaks,
                self.tot_ranks,
                -self.s_adj_speaks,
                self.s_adj_ranks,
                -self.d_adj_speaks,
                self.d_adj_ranks)
