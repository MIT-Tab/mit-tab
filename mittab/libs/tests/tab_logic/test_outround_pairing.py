import random

from django.test import TestCase
from django.core.cache import cache
import pytest

from mittab.apps.tab.models import *
from mittab.libs import outround_tab_logic


@pytest.mark.django_db
class TestOutroundPairingLogic(TestCase):
    fixtures = ["testing_finished_db"]
    pytestmark = pytest.mark.django_db(transaction=True)

    def generate_checkins(self):
        for r in Room.objects.all():
            RoomCheckIn.objects.create(room=r,
                                       round_number=0)

    def test_break(self):
        self.generate_checkins()

        outround_tab_logic.perform_the_break()

    def test_pairing(self):
        self.generate_checkins()

        outround_tab_logic.perform_the_break()
        outround_tab_logic.pair(BreakingTeam.NOVICE)
        outround_tab_logic.pair(BreakingTeam.VARSITY)

    def enter_results(self, type_of_round):
        outrounds = Outround.objects.filter(type_of_round=type_of_round).all()

        for outround in outrounds:
            if not outround.victor:
                outround.victor = random.randint(1, 2)
                outround.save()

    def confirm_pairing(self, outrounds, num_teams):
        for outround in outrounds:
            assert (outround.gov_team.breaking_team.effective_seed +
                    outround.opp_team.breaking_team.effective_seed) == (num_teams + 1)

    def test_all_outrounds(self):
        self.generate_checkins()

        outround_tab_logic.perform_the_break()

        var_teams_to_break = TabSettings.get("var_teams_to_break", 8)

        while var_teams_to_break > 2:
            outround_tab_logic.pair(BreakingTeam.VARSITY)

            outrounds = Outround.objects.filter(
                type_of_round=BreakingTeam.VARSITY,
                num_teams=var_teams_to_break
            )

            self.confirm_pairing(
                outrounds, var_teams_to_break
            )

            self.enter_results(BreakingTeam.VARSITY)

            var_teams_to_break /= 2

    def test_partials(self):
        self.generate_checkins()

        TabSettings.set("var_teams_to_break", 7)

        outround_tab_logic.perform_the_break()

        var_teams_to_break = 8

        while var_teams_to_break > 2:
            outround_tab_logic.pair(BreakingTeam.VARSITY)

            outrounds = Outround.objects.filter(
                type_of_round=BreakingTeam.VARSITY,
                num_teams=var_teams_to_break
            )

            self.confirm_pairing(
                outrounds, var_teams_to_break
            )

            self.enter_results(BreakingTeam.VARSITY)

            var_teams_to_break /= 2
    

