from django.test import TestCase
from mittab.apps.tab.models import Debater
from mittab.apps.tab.models import Team
import mittab.libs.tab_logic as tab_logic
from mittab.libs.tests.data.load_data import load_debater_rankings
from mittab.libs.tests.data.load_data import load_team_rankings
from mittab.libs.tests.assertion import assert_nearly_equal


class TabLogicTestCase(TestCase):
    """Tests that the Tab Logic instance returns sane results"""
    fixtures = ['testing_finished_db']

    def test_debater_score(self):
        """ Comprehensive test of ranking calculations, done on real world
        data that has real world problems (e.g. teams not paired in, ironmen,
        etc ...)
        """
        debaters = Debater.objects.all()
        scores = [(debater.name, tab_logic.debater_score(debater))
                  for debater in debaters]
        expected_scores = load_debater_rankings()
        dict_scores, dict_expected_scores = map(dict,
                                                (scores, expected_scores))
        assert len(dict_scores) == len(dict_expected_scores)
        for k in dict_scores:
            left, right = dict_scores[k], dict_expected_scores[k]
            [assert_nearly_equal(*pair) for pair in zip(set(left), set(right))]


    def test_team_score(self):
        """ Comprehensive test of team scoring calculations, done on real
        world data that has real world inaccuracies """
        teams = Team.objects.all()
        scores = [(team.name, tab_logic.team_score(team)) for team in teams]
        expected_scores = load_team_rankings()
        dict_scores, dict_expected_scores = map(dict,
                                                (scores, expected_scores))
        assert len(dict_scores) == len(dict_expected_scores)
        for k in dict_scores:
            left, right = dict_scores[k], dict_expected_scores[k]
            [assert_nearly_equal(*pair) for pair in zip(set(left), set(right))]






