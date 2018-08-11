import csv

from django.core.management.base import BaseCommand

from mittab.apps.tab.models import Team, Debater
from mittab.libs import tab_logic


class Command(BaseCommand):
    TEAM_ROWS = ('Team Name', 'School', 'Hyrbid School', 'Debater 1', 'Debater 2', 'Wins', 'Speaks', 'Ranks')
    DEBATER_ROWS = ('Name', 'School', 'Speaks', 'Ranks')

    help = 'Dump novice & varsity team/speaker rankings as a csv'

    def make_team_row(self, team):
        return (
            team.name,
            team.school.name,
            team.hybrid_school.name if team.hybrid_school else '',
            team.debaters.first().name if team.debaters.count() else '',
            team.debaters.last().name if team.debaters.count() > 1 else '',
            tab_logic.tot_wins(team),
            tab_logic.tot_speaks(team),
            tab_logic.tot_ranks(team)
        )

    def make_debater_row(self, debater):
        return (
            debater.name,
            tab_logic.deb_team(debater).name if tab_logic.deb_team(debater) else '',
            tab_logic.tot_speaks_deb(debater),
            tab_logic.tot_ranks_deb(debater)
        )

    def write_to_csv(self, filename, headers, rows):
        with open(filename, "w") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

    def handle(self, *args, **kwargs):
        print('Calculating ranks')
        teams = [ self.make_team_row(team) for team in tab_logic.rank_teams() ]
        nov_teams = [ self.make_team_row(team) for team in tab_logic.rank_nov_teams() ]
        debaters = [ self.make_debater_row(deb) for deb in tab_logic.rank_speakers() ]
        nov_debaters = [ self.make_debater_row(deb) for deb in tab_logic.rank_nov_speakers() ]

        print('Writing to csv')
        self.write_to_csv("teams.csv", self.TEAM_ROWS, teams)
        self.write_to_csv("nov-teams.csv", self.TEAM_ROWS, nov_teams)
        self.write_to_csv("debaters.csv", self.DEBATER_ROWS, debaters)
        self.write_to_csv("nov-debaters.csv", self.DEBATER_ROWS, nov_debaters)
        print('Done!')
