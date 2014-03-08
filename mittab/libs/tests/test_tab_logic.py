from django.test import TestCase
from mittab.apps.tab.models import Debater
from mittab.apps.tab.models import Team
from mittab.apps.tab.models import TabSettings
from mittab.apps.tab.models import Round
import mittab.libs.assign_judges as assign_judges
import mittab.libs.tab_logic as tab_logic
from mittab.libs.tests.assertion import assert_nearly_equal
from mittab.libs.tests.data.load_data import load_debater_rankings
from mittab.libs.tests.data.load_data import load_team_rankings
from mittab.libs.tests.helpers import generate_results


class TestRankingLogic(TestCase):
    """Tests that the methods related to debater and team scoring work as
    expected"""
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

class TestPairingLogic(TestCase):
    """Tests that the the generate pairings are correct"""
    fixtures = ['testing_db']

    def pair_round(self):
        tab_logic.pair_round()
        current_round = TabSettings.objects.get(key='cur_round')
        current_round.value = current_round.value + 1
        current_round.save()

    def assign_judges(self):
        cur_round = self.round_number()
        panel_points = []
        rounds = list(Round.objects.filter(round_number=cur_round))
        judges = [ci.judge for ci in
                  CheckIn.objects.filter(round_number=cur_round)]
        assign_judges.add_judges(rounds, judges, panel_points)

    def round_number(self):
        return TabSettings.objects.get(key='cur_round').value - 1

    def test_pairing_tournament(self):
        """
        Tests that we can pair round 1 through round 5, checking for various
        things along the way. This is all one big method because the goal is to
        test the entire pipeline in one go, and make sure a full tournament
        works. Also, test fixture loading is painfully slow
        """
        assert self.round_number() == 0
        self.pair_round()
        assert self.round_number() == 1
        self.assign_judges()
        generate_results(self.round_number(), 0.05, 0.05)
        assert tab_logic.ready_to_pair(self.round_number() + 1)

