import random

from django.test import TestCase
import pytest

from mittab.apps.tab.models import *
from mittab.libs import outround_tab_logic


@pytest.mark.django_db
class TestOutroundPairingLogic(TestCase):
    fixtures = ["testing_finished_db"]
    pytestmark = pytest.mark.django_db

    def generate_checkins(self):
        for r in Room.objects.all():
            RoomCheckIn.objects.create(room=r,
                                       round_number=0)

    def test_break(self):
        self.generate_checkins()

        outround_tab_logic.perform_the_break()

    def test_pairing(self, round_number, last):
        self.generate_checkins()

        outround_tab_logic.perform_the_break()
        outround_tab_logic.pair(BreakingTeam.NOVICE)
        outround_tab_logic.pair(BreakingTeam.VARSITY)
    
