from django.test import TestCase
from mittab.apps.tab.models import CheckIn
from mittab.apps.tab.models import Debater
from mittab.apps.tab.models import Judge
from mittab.apps.tab.models import Team
from mittab.apps.tab.models import TabSettings
from mittab.apps.tab.models import Round
import mittab.libs.assign_judges as assign_judges
import mittab.libs.tab_logic as tab_logic
from mittab.libs.tests.assertion import assert_nearly_equal
from mittab.libs.tests.data.load_data import load_debater_rankings
from mittab.libs.tests.data.load_data import load_team_rankings
from mittab.libs.tests.helpers import generate_results

import random


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
        import pprint
        pprint.pprint( scores)
        expected_scores = load_debater_rankings()
        dict_scores, dict_expected_scores = map(dict,
                                                (scores, expected_scores))
        assert len(dict_scores) == len(dict_expected_scores)
        for k in dict_scores:
            left, right = dict_scores[k], dict_expected_scores[k]
            msg = "{} - {}, {}".format(k, left, right)
            [assert_nearly_equal(*pair, message=msg) for pair
             in zip(set(left), set(right))]

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
            msg = "{} - {}, {}".format(k, left, right)
            [assert_nearly_equal(*pair, message=msg) for pair
             in zip(set(left), set(right))]

class TestPairingLogic(TestCase):
    """
    Tests that the the generated pairings are correct starting from round 1
    """
    fixtures = ['testing_db']

    def pair_round(self):
        tab_logic.pair_round()
        current_round = TabSettings.objects.get(key='cur_round')
        current_round.value = current_round.value + 1
        current_round.save()

    def generate_checkins(self):
        cur_round = self.round_number()
        round_count = Round.objects.filter(round_number=cur_round).count()
        desired_judges = int(round(round_count * 1.2))
        checkin_count = CheckIn.objects.filter(round_number=cur_round).count()

        available = Judge.objects.exclude(judges__round_number=cur_round)
        available = available.filter(checkin__round_number=cur_round)
        available = list(available)
        random.shuffle(available)
        if checkin_count < desired_judges:
            num_to_checkin = desired_judges - checkin_count
            judges_to_checkin = available[:num_to_checkin]
            checkins = [CheckIn(judge=judge, round_number=cur_round) for
                        judge in judges_to_checkin]
            for checkin in checkins:
                checkin.save()


    def assign_judges(self):
        cur_round = self.round_number()
        panel_points = []
        rounds = list(Round.objects.filter(round_number=cur_round))
        self.generate_checkins()
        judges = [ci.judge for ci in
                  CheckIn.objects.filter(round_number=cur_round)]
        print len(rounds), len(judges)
        assign_judges.add_judges(rounds, judges, panel_points)

    def round_number(self):
        return TabSettings.objects.get(key='cur_round').value - 1

    def check_pairing(self, round_number, last):
        assert self.round_number() == round_number
        self.pair_round()
        assert self.round_number() == round_number + 1
        self.assign_judges()
        generate_results(round_number + 1, 0.05, 0.05)
        if round_number + 2 != last:
            assert tab_logic.ready_to_pair(round_number + 2)

    def test_pairing_tournament(self):
        """
        Tests that we can pair round 1 through round 5, checking for various
        things along the way. This is all one big method because the goal is to
        test the entire pipeline in one go, and make sure a full tournament
        works. Also, test fixture loading is painfully slow and we really only
        need it to set the initial stte of the tournament with real world
        data.
        """
        last_round = 6
        for _ in range(1, last_round):
            self.check_pairing(self.round_number(), last_round)


