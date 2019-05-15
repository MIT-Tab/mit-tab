import random

from django.test import TestCase

from mittab.apps.tab.models import CheckIn, Judge, TabSettings, Round
from mittab.libs import assign_judges
from mittab.libs import tab_logic
from mittab.libs.tests.helpers import generate_results


class TestPairingLogic(TestCase):
    """
    Tests that the the generated pairings are correct starting from round 1
    """

    fixtures = ["testing_db"]

    def pair_round(self):
        tab_logic.pair_round()
        current_round = TabSettings.objects.get(key="cur_round")
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
            checkins = [
                CheckIn(judge=judge, round_number=cur_round)
                for judge in judges_to_checkin
            ]
            for checkin in checkins:
                checkin.save()

    def assign_judges(self):
        cur_round = self.round_number()
        panel_points = []
        rounds = list(Round.objects.filter(round_number=cur_round))
        self.generate_checkins()
        judges = [
            ci.judge for ci in CheckIn.objects.filter(round_number=cur_round)
        ]
        assign_judges.add_judges(rounds, judges, panel_points)

    def round_number(self):
        return TabSettings.objects.get(key="cur_round").value - 1

    def check_pairing(self, round_number, last):
        assert self.round_number() == round_number
        self.pair_round()
        assert self.round_number() == round_number + 1
        self.assign_judges()
        generate_results(round_number + 1, 0.05, 0.05)
        if round_number + 2 != last:
            tab_logic.validate_round_data(round_number + 2)

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
