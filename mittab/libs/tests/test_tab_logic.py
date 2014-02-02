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
        debaters = Debater.objects.all()
        expected_scores = [
            (-126.25, 10.0, -76.25, 5.0, -25.5, 1.0),
            (-125.75, 14.0, -75.25, 8.0, -25.0, 3.0),
            (-102.5, 10.5, -76.5, 6.0, -25.5, 2.0),
            (-101.0, 14.5, -74.5, 9.5, -25.0, 3.0),
            (-98.5, 17.5, -73.5, 11.5, -24.5, 4.0)
        ]
        scores = [(debater, tab_logic.debater_score(debater)) for debater in debaters]



