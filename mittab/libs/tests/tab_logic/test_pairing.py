import random

from django.test import TestCase
from django.core.cache import cache
import pytest

from mittab.apps.tab.models import CheckIn, Judge, Room, RoomCheckIn,\
    TabSettings, Round, Team
from mittab.libs import assign_judges, assign_rooms
from mittab.libs import tab_logic
from mittab.libs.tests.helpers import generate_results


@pytest.mark.django_db
class TestPairingLogic(TestCase):
    """
    Tests that the the generated pairings are correct starting from round 1
    """

    fixtures = ["testing_db"]
    pytestmark = pytest.mark.django_db(transaction=True)

    def pair_round(self):
        tab_logic.pair_round()
        current_round = TabSettings.objects.get(key="cur_round")
        current_round.value = current_round.value + 1
        current_round.save()

    def generate_checkins(self, last_round):
        CheckIn.objects.all().delete()
        RoomCheckIn.objects.all().delete()

        judges = list(Judge.objects.all())
        rooms = list(Room.objects.all())
        checkins =[
            CheckIn(judge=j, round_number=round_number)
            for round_number in range(0, last_round + 1)
            for j in judges
        ]
        room_checkins = [
            RoomCheckIn(room=r, round_number=round_number)
            for round_number in range(0, last_round + 1)
            for r in rooms
        ]
        CheckIn.objects.bulk_create(checkins)
        RoomCheckIn.objects.bulk_create(room_checkins)

    def assign_judges_to_pairing(self):
        assign_judges.add_judges()

    def assign_rooms_to_pairing(self):
        assign_rooms.add_rooms()

    def round_number(self):
        return TabSettings.get("cur_round") - 1

    def check_pairing(self, round_number, last):
        assert self.round_number() == round_number
        self.pair_round()
        assert self.round_number() == round_number + 1
        self.assign_judges_to_pairing()
        self.assign_rooms_to_pairing()
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
        TabSettings.set("cur_round", 1)
        self.generate_checkins(last_round)
        for _ in range(1, last_round):
            round_number = self.round_number()
            self.check_pairing(round_number, last_round)
