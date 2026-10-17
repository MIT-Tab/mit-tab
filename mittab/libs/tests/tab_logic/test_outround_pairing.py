import random

from django.test import TestCase
from django.db.models import Q
import pytest

from mittab.apps.tab.models import *
from mittab.apps.tab.forms import OutroundResultEntryForm
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

    def test_break_uses_defaults_when_break_settings_missing(self):
        self.generate_checkins()
        for setting in TabSettings.objects.filter(
                key__in=["var_teams_to_break", "nov_teams_to_break"]):
            setting.delete()

        success, message = outround_tab_logic.perform_the_break()

        self.assertTrue(success, message)
        self.assertEqual(BreakingTeam.objects.filter(
            type_of_team=BreakingTeam.VARSITY
        ).count(), 8)
        self.assertTrue(BreakingTeam.objects.filter(
            type_of_team=BreakingTeam.NOVICE
        ).exists())

    def test_break_can_disable_novice_break(self):
        self.generate_checkins()
        TabSettings.set("nov_teams_to_break", 0)

        success, message = outround_tab_logic.perform_the_break()

        self.assertTrue(success, message)
        self.assertFalse(BreakingTeam.objects.filter(
            type_of_team=BreakingTeam.NOVICE
        ).exists())
        self.assertTrue(BreakingTeam.objects.filter(
            type_of_team=BreakingTeam.VARSITY
        ).exists())

    def test_break_can_disable_varsity_break(self):
        self.generate_checkins()
        TabSettings.set("var_teams_to_break", 0)

        success, message = outround_tab_logic.perform_the_break()

        self.assertTrue(success, message)
        self.assertFalse(BreakingTeam.objects.filter(
            type_of_team=BreakingTeam.VARSITY
        ).exists())
        self.assertTrue(BreakingTeam.objects.filter(
            type_of_team=BreakingTeam.NOVICE
        ).exists())

    def enter_results(self, type_of_round):
        outrounds = Outround.objects.filter(type_of_round=type_of_round).all()

        for outround in outrounds:
            if not outround.victor:
                outround.victor = random.randint(1, 2)
                outround.save()

    def save_result(self, outround, winner):
        form = OutroundResultEntryForm(
            data={
                "winner": winner,
                "round_instance": outround.id,
            },
            round_instance=outround,
        )
        assert form.is_valid(), form.errors
        form.save()

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

    def test_challenge_pick_affects_next_round_pairing(self):
        self.generate_checkins()
        TabSettings.set("outround_pairing_mode", 1)
        TabSettings.set("challenge_seed_basis", 1)

        outround_tab_logic.perform_the_break()
        outround_tab_logic.pair(BreakingTeam.VARSITY)
        ro8_rounds = Outround.objects.filter(
    type_of_round=BreakingTeam.VARSITY, num_teams=8)
        for outround in ro8_rounds:
            winning_side = Outround.GOV \
                if outround.gov_team.breaking_team.effective_seed < outround.opp_team.breaking_team.effective_seed \
                else Outround.OPP
            self.save_result(outround, winning_side)

        one_seed = BreakingTeam.objects.get(
    type_of_team=BreakingTeam.VARSITY, effective_seed=1)
        two_seed = BreakingTeam.objects.get(
    type_of_team=BreakingTeam.VARSITY, effective_seed=2)
        outround_tab_logic.save_challenge_pick(
            BreakingTeam.VARSITY,
            4,
            one_seed.team.id,
            two_seed.team.id,
        )

        outround_tab_logic.pair(BreakingTeam.VARSITY)
        ro4_rounds = Outround.objects.filter(
    type_of_round=BreakingTeam.VARSITY, num_teams=4)
        assert ro4_rounds.filter(
            Q(gov_team=one_seed.team, opp_team=two_seed.team)
            | Q(gov_team=two_seed.team, opp_team=one_seed.team)
        ).exists()

    def test_original_seed_challenge_mode_keeps_original_seed_on_upsets(self):
        self.generate_checkins()
        TabSettings.set("outround_pairing_mode", 1)
        TabSettings.set("challenge_seed_basis", 0)
        outround_tab_logic.perform_the_break()
        outround_tab_logic.pair(BreakingTeam.VARSITY)

        outround = Outround.objects.filter(
    type_of_round=BreakingTeam.VARSITY,
     num_teams=8).first()
        gov_seed = outround.gov_team.breaking_team.effective_seed
        opp_seed = outround.opp_team.breaking_team.effective_seed
        winning_side = Outround.GOV if gov_seed > opp_seed else Outround.OPP
        upset_winner = outround.gov_team if winning_side == Outround.GOV else outround.opp_team
        winner_seed_before = upset_winner.breaking_team.seed

        self.save_result(outround, winning_side)
        upset_winner.refresh_from_db()
        assert upset_winner.breaking_team.seed == winner_seed_before
        assert upset_winner.breaking_team.effective_seed == min(gov_seed, opp_seed)
