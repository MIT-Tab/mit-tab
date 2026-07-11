from django.db import transaction
from django.test import TestCase
import pytest

from mittab.apps.tab.models import (
    CheckIn,
    Debater,
    Judge,
    Room,
    RoomCheckIn,
    Round,
    RoundStats,
    School,
    TabSettings,
    Team,
)
from mittab.libs import assign_judges, assign_rooms, errors
from mittab.libs import tab_logic
from mittab.libs.cacheing import cache_logic
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

        cache_logic.clear_cache()
        tab_logic.pair_round()

        current_round.refresh_from_db()
        current_round.value = round_to_pair + 1
        current_round.save()
        return round_to_pair

    def generate_checkins(self, round_number):
        CheckIn.objects.all().delete()
        RoomCheckIn.objects.all().delete()

        judges = list(Judge.objects.all())
        rooms = list(Room.objects.all())
        checkins = [
            CheckIn(judge=j, round_number=rnd)
            for rnd in range(0, round_number + 1)
            for j in judges
        ]
        room_checkins = [
            RoomCheckIn(room=r, round_number=rnd)
            for rnd in range(0, round_number + 1)
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


@pytest.mark.django_db
class TestRoundTwoRandomWithinBrackets(TestCase):
    pytestmark = pytest.mark.django_db

    def setUp(self):
        super().setUp()
        self.school = School.objects.create(name="Pairing Test School")
        TabSettings.set("cur_round", 2)
        TabSettings.set("tot_rounds", 5)
        TabSettings.set("r2_random_within_brackets", 1)
        cache_logic.clear_cache()

    def make_teams(self, count):
        teams = []
        for team_num in range(count):
            team = Team.objects.create(
                name=f"Team {team_num}",
                school=self.school,
                seed=Team.FULL_SEED,
            )
            debaters = [
                Debater.objects.create(
                    name=f"Debater {team_num}-{debater_num}",
                    novice_status=Debater.VARSITY,
                    school=self.school,
                )
                for debater_num in range(2)
            ]
            team.debaters.add(*debaters)
            teams.append(team)
        return teams

    def make_round_one(self, gov, opp, victor, gov_speaks, opp_speaks):
        round_obj = Round.objects.create(
            round_number=1,
            gov_team=gov,
            opp_team=opp,
            victor=victor,
        )
        for team, speaks in ((gov, gov_speaks), (opp, opp_speaks)):
            for rank, debater in enumerate(team.debaters.all(), start=1):
                RoundStats.objects.create(
                    debater=debater,
                    round=round_obj,
                    speaks=speaks / 2,
                    ranks=rank,
                    debater_role="pm" if rank == 1 else "mg",
                )
        return round_obj

    def make_checkins(self, rooms):
        judges = [
            Judge.objects.create(name=f"Judge {judge_num}", rank=5)
            for judge_num in range(rooms)
        ]
        rooms = [
            Room.objects.create(name=f"Room {room_num}", rank=5)
            for room_num in range(rooms)
        ]
        CheckIn.objects.bulk_create(
            CheckIn(judge=judge, round_number=2) for judge in judges
        )
        RoomCheckIn.objects.bulk_create(
            RoomCheckIn(room=room, round_number=2) for room in rooms
        )

    def pairing_sets(self):
        return {
            frozenset((round_obj.gov_team_id, round_obj.opp_team_id))
            for round_obj in Round.objects.filter(round_number=2)
        }

    def test_round_two_random_brackets_do_not_rematch_round_one(self):
        teams = self.make_teams(8)
        self.make_checkins(4)
        self.make_round_one(teams[0], teams[4], Round.GOV, 60, 52)
        self.make_round_one(teams[1], teams[5], Round.GOV, 58, 50)
        self.make_round_one(teams[2], teams[6], Round.GOV, 56, 48)
        self.make_round_one(teams[3], teams[7], Round.GOV, 54, 46)

        tab_logic.pair_round()

        round_one_pairs = {
            frozenset((teams[0].id, teams[4].id)),
            frozenset((teams[1].id, teams[5].id)),
            frozenset((teams[2].id, teams[6].id)),
            frozenset((teams[3].id, teams[7].id)),
        }
        high_low_pairs = {
            frozenset((teams[0].id, teams[3].id)),
            frozenset((teams[1].id, teams[2].id)),
            frozenset((teams[4].id, teams[7].id)),
            frozenset((teams[5].id, teams[6].id)),
        }
        self.assertEqual(Round.objects.filter(round_number=2).count(), 4)
        self.assertFalse(self.pairing_sets() & round_one_pairs)
        self.assertNotEqual(self.pairing_sets(), high_low_pairs)

    def test_round_two_pullup_uses_lowest_lower_bracket_team(self):
        teams = self.make_teams(6)
        self.make_checkins(3)
        self.make_round_one(teams[0], teams[3], Round.GOV, 60, 54)
        self.make_round_one(teams[1], teams[4], Round.GOV, 58, 52)
        self.make_round_one(teams[2], teams[5], Round.GOV, 56, 50)

        tab_logic.pair_round()

        pullup_round = Round.objects.get(round_number=2, pullup__in=[
            Round.GOV,
            Round.OPP,
        ])
        pullup_team = (
            pullup_round.gov_team
            if pullup_round.pullup == Round.GOV
            else pullup_round.opp_team
        )
        round_one_pairs = {
            frozenset((teams[0].id, teams[3].id)),
            frozenset((teams[1].id, teams[4].id)),
            frozenset((teams[2].id, teams[5].id)),
        }
        pullup_pair = frozenset((
            pullup_round.gov_team_id,
            pullup_round.opp_team_id,
        ))
        self.assertEqual(pullup_team, teams[5])
        self.assertNotIn(pullup_pair, round_one_pairs)

    def test_random_pairing_no_repeats_raises_without_full_matching(self):
        teams = self.make_teams(2)
        self.make_round_one(teams[0], teams[1], Round.GOV, 60, 54)

        with self.assertRaises(errors.NotEnoughTeamsError):
            tab_logic.random_pairing_no_repeats(teams)
