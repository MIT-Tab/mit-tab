from optparse import make_option

from django.core.management.base import BaseCommand
from mittab.apps.tab.models import NoShow, RoundStats, TabSettings, Team, Debater
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
        if len(speaks) > 0:
            percentiles = np.percentile(speaks, [0, 25, 50, 75, 100])
            mean, std = round(np.mean(speaks), 2), round(np.std(speaks), 2)
        else:
            percentiles = [0]*5
            mean, std = 0, 0
        print "{0} ".format(header),
        print (("| %05.2f "*5) + "|") % tuple(percentiles),"|",
        print (("|  %05.2f  "*2) + "|") % (mean, std)
        return percentiles, mean, std

    def valid_speaks_for_round(self, round_number):
        return RoundStats.objects.filter(round__round_number=round_number)\
                                 .filter(speaks__gte=20).all()

    def deb_speaks_for_round(self, round_number, debater_status):
        return RoundStats.objects.filter(round__round_number=round_number)\
                                 .filter(speaks__gte=20)\
                                 .filter(debater__novice_status=debater_status)

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
            results[sbracket].append(float(speak.speaks))
        return results

    def bracket_stats(self, speaks):
        bracket_results = {}
        bucketed_speaks = self.bucket_speaks_by_wl(speaks)
        for index, speak_values in enumerate(bucketed_speaks):
            bracket_results["{0} up ({1:3d})".format(index, int(len(speak_values)))] = \
            self.speaks_stats(
                    speak_values,
                    "{0} up ({1:3d})".format(index,
                        int(len(speak_values))))
        return bracket_results

    def print_header(self, header):
        print header
        print "            |  min  |  25%  |  50%  |  75%  |  max  | | |   mean  | stddev |" 

    def handle(self, *args, **options):
        stats = {}
        valid_speaks = RoundStats.objects.filter(speaks__gte=20)
        novice_speaks = []
        varsity_speaks = []
        for speak in valid_speaks:
            if speak.debater.novice_status == Debater.VARSITY:
                varsity_speaks.append(float(speak.speaks))
            else:
                novice_speaks.append(float(speak.speaks))

        speaks = [float(rs.speaks) for rs in valid_speaks]
        print "#" * 80
        print "Tournament Statistics:"
        print "1) Number of teams: {0} competed out of {1} regged".format(*self.count_valid_teams())
        print "#" * 80
        print "Speaking Statistics:"
        self.print_header("Speaking statistics for the entire tournament")
        stats["overall"] = {}
        stats["overall"]["Combined"] = self.speaks_stats(speaks,        "Combined  ")
        stats["overall"]["Varsity"] = self.speaks_stats(varsity_speaks, "Varsity   ")
        stats["overall"]["Novice"] = self.speaks_stats(novice_speaks,   "Novice    ")

        stats["round_5"] = {}
        self.print_header("Combined Speaking Statistics in Round 5")
        stats["round_5"]["Combined"] = self.bracket_stats(self.valid_speaks_for_round(5))
        self.print_header("Varsity Speaking Statistics in Round 5")
        stats["round_5"]["Varsity"] = self.bracket_stats(self.deb_speaks_for_round(5, Debater.VARSITY))
        self.print_header("Novice Speaking Statistics in Round 5")
        stats["round_5"]["Novice"] = self.bracket_stats(self.deb_speaks_for_round(5, Debater.NOVICE))

        def make_csv(speak_stats):
            data = list(speak_stats[0])
            data.extend(speak_stats[1:])
            return ','.join(["%05.2f" % d for d in data])

        output = "Teams,{0} competed,{1} registered\n".format(*self.count_valid_teams())
        output += "Overall Statistics,min,25%,50%,75%,max,mean,stddev\n"
        for key in sorted(stats["overall"]):
            value = stats["overall"][key]
            output += key + "," + make_csv(value) + "\n"

        for key in sorted(stats["round_5"]):
            value = stats["round_5"][key]
            output += key + " Speaking Statistics in Round 5,min,25%,50%,75%,max,mean,stddev\n"
            rounds = []
            for round_number, vvalue in value.iteritems():
                rounds.append((round_number, vvalue))
            rounds.sort()
            for round_number, vvalue in rounds:
                output += round_number + "," + make_csv(vvalue) + "\n"

        print args
        if len(args) == 1:
            with open(args[0], 'w') as f:
                f.write(output)







