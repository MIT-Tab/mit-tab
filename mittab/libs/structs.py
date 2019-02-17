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
