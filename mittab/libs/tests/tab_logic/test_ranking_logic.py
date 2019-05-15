from django.test import TestCase

from mittab.apps.tab.models import Debater, Team
from mittab.libs.tests.assertion import assert_nearly_equal
from mittab.libs.tests.data.load_data import load_debater_rankings
from mittab.libs.tests.data.load_data import load_team_rankings
from mittab.libs.tab_logic.rankings import TeamScore, DebaterScore


class TestRankingLogic(TestCase):
    """Tests that the methods related to debater and team scoring work as
    expected"""

    fixtures = ["testing_finished_db"]

    def test_debater_score(self):
        """ Comprehensive test of ranking calculations, done on real world
        data that has real world problems (e.g. teams not paired in, ironmen,
        etc ...)
        """
        debaters = Debater.objects.all()
        scores = [(debater.name, DebaterScore(debater).scoring_tuple())
                  for debater in debaters]
        expected_scores = load_debater_rankings()
        dict_scores, dict_expected_scores = list(
            map(dict, (scores, expected_scores)))

        assert len(dict_scores) == len(dict_expected_scores)
        for k in dict_scores:
            left, right = dict_scores[k], dict_expected_scores[k]
            msg = "{} - {}, {}".format(k, left, right)
            [
                assert_nearly_equal(*pair, message=msg)
                for pair in zip(set(left), set(right))
            ]

    def test_team_score(self):
        """ Comprehensive test of team scoring calculations, done on real
        world data that has real world inaccuracies """
        teams = Team.objects.all()
        scores = [(team.name, TeamScore(team).scoring_tuple())
                  for team in teams]
        expected_scores = load_team_rankings()
        dict_scores, dict_expected_scores = list(
            map(dict, (scores, expected_scores)))
        assert len(dict_scores) == len(dict_expected_scores)
        for k in dict_scores:
            left, right = dict_scores[k], dict_expected_scores[k]
            msg = "{} - {}, {}".format(k, left, right)
            [
                assert_nearly_equal(*pair, message=msg)
                for pair in zip(set(left), set(right))
            ]
