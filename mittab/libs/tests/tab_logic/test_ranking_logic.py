from django.test import TestCase
import pytest

from mittab.apps.tab.models import Debater, TabSettings, Team
from mittab.libs.tests.assertion import assert_nearly_equal
from mittab.libs.tests.data.load_data import load_debater_rankings
from mittab.libs.tests.data.load_data import load_team_rankings
from mittab.libs.tab_logic.rankings import (
    COIN_FLIP,
    DOUBLE_ADJUSTED_RANKS,
    DOUBLE_ADJUSTED_SPEAKS,
    RANKS,
    SINGLE_ADJUSTED_RANKS,
    SINGLE_ADJUSTED_SPEAKS,
    SPEAKER_SINGLE_ADJUSTED_RANKINGS_SETTING,
    SPEAKS,
    WINS,
    DebaterScore,
    TeamScore,
    speaker_stat_priority,
)


@pytest.mark.django_db
class TestRankingLogic(TestCase):
    """Tests that the methods related to debater and team scoring work as
    expected"""

    fixtures = ["testing_finished_db"]
    pytestmark = pytest.mark.django_db(transaction=True)

    def debater_score_with_stats(self, speaks, ranks, adjusted_speaks,
                                 adjusted_ranks, stat_priority=None):
        score = DebaterScore.__new__(DebaterScore)
        if stat_priority is not None:
            score.stat_priority = stat_priority
        score.stats = {
            SPEAKS: speaks,
            RANKS: ranks,
            SINGLE_ADJUSTED_SPEAKS: adjusted_speaks,
            SINGLE_ADJUSTED_RANKS: adjusted_ranks,
            DOUBLE_ADJUSTED_SPEAKS: adjusted_speaks,
            DOUBLE_ADJUSTED_RANKS: adjusted_ranks,
            COIN_FLIP: 0,
        }
        return score

    def test_debater_score(self):
        """ Comprehensive test of ranking calculations, done on real world
        data that has real world problems (e.g. teams not paired in, ironmen,
        etc ...)
        """
        TabSettings.set("cur_round", 6)
        debaters = Debater.objects.order_by("pk")
        actual_scores = [(debater.name,
                          DebaterScore(debater).scoring_tuple()[:6])
                         for debater in debaters]
        actual_scores = dict(actual_scores)
        expected_scores = dict(load_debater_rankings())
        assert len(expected_scores) == len(actual_scores)
        for name, actual_score in actual_scores.items():
            left, right = actual_score, expected_scores[name]
            msg = f"{name} - actual: {left}, expected {right}"
            [
                assert_nearly_equal(*pair, message=msg)
                for pair in zip(left, right)
            ]

    def test_debater_score_defaults_to_unadjusted_before_single_adjusted(self):
        stat_priority = speaker_stat_priority(False)
        better_unadjusted = self.debater_score_with_stats(
            100, 1, 90, 1, stat_priority
        )
        better_single_adjusted = self.debater_score_with_stats(
            99, 1, 95, 1, stat_priority
        )

        scores = sorted([better_single_adjusted, better_unadjusted])

        self.assertEqual(scores, [better_unadjusted, better_single_adjusted])

    def test_debater_score_can_use_single_adjusted_before_unadjusted(self):
        TabSettings.set(SPEAKER_SINGLE_ADJUSTED_RANKINGS_SETTING, 1)
        stat_priority = speaker_stat_priority()
        better_unadjusted = self.debater_score_with_stats(
            100, 1, 90, 1, stat_priority
        )
        better_single_adjusted = self.debater_score_with_stats(
            99, 1, 95, 1, stat_priority
        )

        scores = sorted([better_unadjusted, better_single_adjusted])

        self.assertEqual(scores, [better_single_adjusted, better_unadjusted])

    def test_speaker_setting_does_not_change_team_score_priority(self):
        TabSettings.set(SPEAKER_SINGLE_ADJUSTED_RANKINGS_SETTING, 1)

        self.assertEqual(
            TeamScore.stat_priority[:5],
            (WINS, SPEAKS, RANKS, SINGLE_ADJUSTED_SPEAKS,
             SINGLE_ADJUSTED_RANKS),
        )

    def test_team_score(self):
        """ Comprehensive test of team scoring calculations, done on real
        world data that has real world inaccuracies """
        TabSettings.set("cur_round", 6)
        teams = Team.objects.order_by("pk")
        actual_scores = [(team.name, TeamScore(team).scoring_tuple()[:8])
                         for team in teams]
        actual_scores = dict(actual_scores)
        expected_scores = dict(load_team_rankings())
        assert len(actual_scores) == len(expected_scores)
        for team_name, actual_score in actual_scores.items():
            left, right = actual_score, expected_scores[team_name]
            msg = f"{team_name} - actual: {left}, expected: {right}"
            [
                assert_nearly_equal(*pair, message=msg)
                for pair in zip(left, right)
            ]
