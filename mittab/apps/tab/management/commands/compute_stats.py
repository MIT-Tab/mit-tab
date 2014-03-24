from optparse import make_option

from django.core.management.base import BaseCommand
from mittab.apps.tab.models import NoShow, RoundStats, TabSettings, Team
import mittab.libs.tab_logic as tab_logic

import numpy as np


class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option("--output-file", dest="output_file",
                    help="file to output data to as csv"),)

    help = 'Calculate statistics relevant to the tabulation'

    def count_valid_teams(self):
        num_teams, num_valid_teams = 0, 0
        num_rounds = TabSettings.get('tot_rounds')
        for team in Team.objects.all():
            num_teams += 1
            num_forfeits = NoShow.objects.filter(no_show_team=team).count()
            if num_rounds - num_forfeits >= 3:
                num_valid_teams += 1
        return num_valid_teams, num_teams

    def speaks_stats(self, speaks, header):
        if len(speaks) == 0:
            return [], 0, 0
        percentiles = np.percentile(speaks, [0, 25, 50, 75, 100])
        mean, std = round(np.mean(speaks), 2), round(np.std(speaks), 2)
        print header
        print "|  min  |  25%  |  50%  |  75%  |  max  |"
        print (("| %05.2f "*5) + "|") % tuple(percentiles)

        print "Mean, Standard Deviation: {0:.2f}, {1:.2f}".format(mean, std)
        return percentiles, mean, std

    def valid_speaks_for_round(self, round_number):
        return RoundStats.objects.filter(round__round_number=round_number)\
                                 .filter(speaks__gte=20).all()

    def bucket_speaks_by_wl(self, speaks):
        def bracket(round_obj):
            gov_wins, opp_wins = tab_logic.tot_wins(round_obj.gov_team), \
                                 tab_logic.tot_wins(round_obj.opp_team)
            if abs(gov_wins - opp_wins) <= 1:
                return min(gov_wins, opp_wins)
            else:
                return max(gov_wins, opp_wins) - 1

        results = [list() for i in range(5)]
        for speak in speaks:
            sbracket = bracket(speak.round)
            results[sbracket].append(speak)
        return results

    def bracket_stats(self, speaks):
        bracket_results = {}
        bucketed_speaks = self.bucket_speaks_by_wl(speaks)
        for index, speaks in enumerate(bucketed_speaks):
            speak_values = [float(s.speaks) for s in speaks]
            bracket_results[str(index) + 'up'] = \
            self.speaks_stats(
                    speak_values,
                    "Speaks for {0} up bracket, size {1}:".format(index,
                        int(len(speaks)/4.0)))
        return bracket_results

    def handle(self, *args, **options):
        stats = {}
        valid_speaks = RoundStats.objects.filter(speaks__gte=20)
        speaks = [float(rs.speaks) for rs in valid_speaks]
        print "#" * 80
        print "Tournament Statistics:"
        print "1) Number of teams: {0} competed out of {1} regged".format(*self.count_valid_teams())
        print "#" * 80
        print "Speaking Statistics:"
        stats["all"] = self.speaks_stats(speaks, "Combined Speaks for all Rounds")
        stats["round_5"] = self.bracket_stats(self.valid_speaks_for_round(5))

        import pprint
        pprint.pprint(stats)



