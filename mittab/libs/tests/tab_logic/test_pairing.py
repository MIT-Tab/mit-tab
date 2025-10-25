import random

from django.db import transaction
from django.test import TestCase
import pytest

from mittab.apps.tab.models import (
    CheckIn,
    Judge,
    Room,
    RoomCheckIn,
    Round,
    School,
    TabSettings,
    Team,
)
from mittab.libs import assign_judges, assign_rooms, cache_logic
from mittab.libs import tab_logic
from mittab.libs.tests.helpers import generate_results


@pytest.mark.django_db
class TestPairingLogic(TestCase):
    """
    Tests that the the generated pairings are correct starting from round 1
    """

    fixtures = ["testing_db"]
    pytestmark = pytest.mark.django_db

    def setUp(self):
        super().setUp()
        TabSettings.set("cur_round", 1)

    def pair_round(self):
        current_round = TabSettings.objects.get(key="cur_round")
        round_to_pair = current_round.value

        # Ensure we have enough judges/rooms checked in for the upcoming round
        self.generate_checkins(round_number=round_to_pair)

        cache_logic.clear_cache()
        tab_logic.pair_round()

        current_round.refresh_from_db()
        current_round.value = round_to_pair + 1
        current_round.save()
        return round_to_pair

    def generate_checkins(self, round_number=None):
        if round_number is None:
            round_number = self.round_number()

        teams_checked_in = Team.objects.filter(checked_in=True).count()
        desired_pairings = max(1, teams_checked_in // 2) if teams_checked_in else 0
        desired_judges = desired_pairings
        checkin_count = CheckIn.objects.filter(round_number=round_number).count()

        available = Judge.objects.exclude(judges__round_number=round_number)
        available = available.exclude(checkin__round_number=round_number)
        available = list(available)
        random.shuffle(available)
        if checkin_count < desired_judges:
            num_to_checkin = desired_judges - checkin_count
            judges_to_checkin = available[:num_to_checkin]
            for judge in judges_to_checkin:
                CheckIn.objects.get_or_create(
                    judge=judge, round_number=round_number)

        room_checkins = RoomCheckIn.objects.filter(round_number=round_number).count()
        if room_checkins < desired_pairings:
            rooms_needed = desired_pairings - room_checkins
            available_rooms = (
                Room.objects.exclude(roomcheckin__round_number=round_number)
                .order_by("-rank", "id")
            )[:rooms_needed]
            for room in available_rooms:
                RoomCheckIn.objects.create(room=room, round_number=round_number)

    def assign_judges_to_pairing(self):
        self.generate_checkins()
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
        for _ in range(1, last_round):
            self.check_pairing(self.round_number(), last_round)

    # --- Re-pair workflow helpers/tests ---

    def re_pair_latest_round(self):
        cleared_round = tab_logic.clear_current_round_pairing()
        current_round = TabSettings.objects.get(key="cur_round")
        current_round.value = cleared_round
        current_round.save()

        self.pair_round()
        return cleared_round

    def pairings_for(self, round_number):
        return list(
            Round.objects.filter(round_number=round_number)
            .order_by("gov_team_id", "opp_team_id")
            .values_list("gov_team_id", "opp_team_id")
        )

    def mutate_tournament_state(self, previous_round_number):
        teams_to_toggle = Team.objects.filter(checked_in=True).order_by("id")[:2]
        for team in teams_to_toggle:
            team.checked_in = False
            team.save()

        for round_obj in Round.objects.filter(round_number=previous_round_number):
            if round_obj.victor in (Round.GOV, Round.GOV_VIA_FORFEIT):
                round_obj.victor = Round.OPP
            else:
                round_obj.victor = Round.GOV
            round_obj.save()

        school_team = Team.objects.exclude(school=None).order_by("id").first()
        if school_team and School.objects.exclude(id=school_team.school_id).exists():
            new_school = (
                School.objects.exclude(id=school_team.school_id)
                .order_by("id")
                .first()
            )
            school_team.school = new_school
            school_team.save()

    def test_repair_is_deterministic(self):
        paired_round = self.pair_round()
        baseline_pairings = self.pairings_for(paired_round)

        for _ in range(3):
            self.re_pair_latest_round()
            self.assertEqual(self.pairings_for(paired_round), baseline_pairings)

    def test_repair_recovers_after_data_mutations(self):
        first_round = self.pair_round()
        generate_results(first_round, seed="repair")

        second_round = self.pair_round()
        baseline_pairings = self.pairings_for(second_round)

        with transaction.atomic():
            self.mutate_tournament_state(previous_round_number=first_round)
            self.re_pair_latest_round()
            mutated_pairings = self.pairings_for(second_round)
            self.assertNotEqual(mutated_pairings, baseline_pairings)
            transaction.set_rollback(True)

        self.re_pair_latest_round()
        self.assertEqual(self.pairings_for(second_round), baseline_pairings)
