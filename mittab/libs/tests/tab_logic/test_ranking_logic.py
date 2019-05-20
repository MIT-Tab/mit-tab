from django.test import TestCase
import pytest

from mittab.apps.tab.models import Debater, Team
from mittab.libs.tests.assertion import assert_nearly_equal
from mittab.libs.tests.data.load_data import load_debater_rankings
from mittab.libs.tests.data.load_data import load_team_rankings
from mittab.libs.tab_logic.rankings import TeamScore, DebaterScore


@pytest.mark.django_db
class TestRankingLogic(TestCase):
    """Tests that the methods related to debater and team scoring work as
    expected"""

    fixtures = ["testing_finished_db"]
    pytestmark = pytest.mark.django_db

    def test_debater_score(self):
        """ Comprehensive test of ranking calculations, done on real world
        data that has real world problems (e.g. teams not paired in, ironmen,
        etc ...)
        """
        debaters = Debater.objects.order_by("pk")
        actual_scores = [(debater.name,
                          DebaterScore(debater).scoring_tuple()[:6])
                         for debater in debaters]
        actual_scores = dict(actual_scores)
        expected_scores = dict(load_debater_rankings())
        assert len(expected_scores) == len(actual_scores)
        for name, actual_score in actual_scores.items():
            left, right = actual_score, expected_scores[name]
            msg = "{} - actual: {}, expected {}".format(name, left, right)
            [
                assert_nearly_equal(*pair, message=msg)
                for pair in zip(left, right)
            ]

    def test_team_score(self):
        """ Comprehensive test of team scoring calculations, done on real
        world data that has real world inaccuracies """
        teams = Team.objects.order_by("pk")
        actual_scores = [(team.name, TeamScore(team).scoring_tuple()[:8])
                         for team in teams]
        actual_scores = dict(actual_scores)
        expected_scores = dict(load_team_rankings())
        assert len(actual_scores) == len(expected_scores)
        for team_name, actual_score in actual_scores.items():
            left, right = actual_score, expected_scores[team_name]
            msg = "{} - actual: {}, expected: {}".format(
                team_name, left, right)
            [
                assert_nearly_equal(*pair, message=msg)
                for pair in zip(left, right)
            ]
