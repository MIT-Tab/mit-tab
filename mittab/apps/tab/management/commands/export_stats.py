import os
import csv
from optparse import make_option

from django.core.management.base import BaseCommand

from mittab.apps.tab.models import Team, Debater
from mittab.libs import tab_logic


class Command(BaseCommand):
    TEAM_ROWS = ('Team Name', 'School', 'Hyrbid School', 'Debater 1', 'Debater 2', 'Wins', 'Speaks', 'Ranks')
    DEBATER_ROWS = ('Name', 'School', 'Speaks', 'Ranks')

    help = 'Dump novice & varsity team/speaker rankings as a csv'
    option_list = BaseCommand.option_list + (
            make_option("--root", dest="root",
                default=".", help="root path for all of the csv files"),
            make_option("--team-file", dest="team_file",
                default="teams.csv", help="name of the teams file"),
            make_option("--nov-team-file", dest="nov_team_file",
                default="nov-teams.csv", help="name of the novice teams file"),
            make_option("--debater-file", dest="debater_file",
                default="debaters.csv", help="name of the debaters file"),
            make_option("--nov-debater-file", dest="nov_debater_file",
                default="nov-debaters.csv", help="name of the novice debaters file"))

    def make_team_row(self, team):
        return (
            team.name,
            team.school.name,
            team.hybrid_school.name if team.hybrid_school else "",
            team.debaters.first().name if team.debaters.count() else "",
            team.debaters.last().name if team.debaters.count() > 1 else "",
            tab_logic.tot_wins(team),
            tab_logic.tot_speaks(team),
            tab_logic.tot_ranks(team)
        )

    def make_debater_row(self, debater):
        return (
            debater.name,
            tab_logic.deb_team(debater).name if tab_logic.deb_team(debater) else "",
            tab_logic.tot_speaks_deb(debater),
            tab_logic.tot_ranks_deb(debater)
        )

    def write_to_csv(self, filename, headers, rows):
        with open(filename, "w") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

    def handle(self, *args, **kwargs):
        if not os.path.exists(kwargs["root"]): os.makedirs(kwargs["root"])

        print('Calculating ranks')
        teams = [ self.make_team_row(team) for team in tab_logic.rank_teams() ]
        nov_teams = [ self.make_team_row(team) for team in tab_logic.rank_nov_teams() ]
        debaters = [ self.make_debater_row(deb) for deb in tab_logic.rank_speakers() ]
        nov_debaters = [ self.make_debater_row(deb) for deb in tab_logic.rank_nov_speakers() ]

        print('Writing to csv')
        self.write_to_csv(os.path.join(kwargs["root"], kwargs["team_file"]),
                self.TEAM_ROWS, teams)
        self.write_to_csv(os.path.join(kwargs["root"], kwargs["nov_team_file"]),
                self.TEAM_ROWS, nov_teams)
        self.write_to_csv(os.path.join(kwargs["root"], kwargs["debater_file"]),
                self.DEBATER_ROWS, debaters)
        self.write_to_csv(os.path.join(kwargs["root"], kwargs["nov_debater_file"]),
                self.DEBATER_ROWS, nov_debaters)
        print('Done!')
