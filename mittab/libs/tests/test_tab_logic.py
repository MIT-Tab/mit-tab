from django.test import TestCase
import mittab.libs.tab_logic as tab_logic
from mittab.apps.tab.models import Debater
from mittab.apps.tab.models import Team


class TabLogicTestCase(TestCase):
    """Tests that the Tab Logic instance returns sane results"""
    fixtures = ['testing_finished_db']

    def test_highest_seed(self):
        teams = Team.objects.all()
        team1, team2 = teams[:2]

    def test_debater_score(self):
        """ Comprehensive test of ranking calculations, done on real world
        data that has real world problems (e.g. teams not paired in, ironmen,
        etc ...)
        """
        debaters = Debater.objects.all()
        scores = [(debater, tab_logic.debater_score(debater)) for debater in debaters]

    def test_team_score(self):
        """ Comprehensive test of team scoring calculations, done on real
        world data that has real world inaccuracies """
        teams = Team.objects.all()
        team_scores = [(team, tab_logic.team_score(team)) for team in teams]





