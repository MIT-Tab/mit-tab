from decimal import Decimal

from django.db import transaction
from django.test import TestCase
import pytest

from mittab.apps.tab.models import (
    TeamTeamScratch, JudgeJudgeScratch, Scratch,
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
from mittab.libs.tests.helpers import generate_results, clear_all_scratches


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


@pytest.mark.django_db
class TestScratchHonoring(TestCase):
    """Test that scratches are properly honored during pairing"""

    fixtures = ["testing_db"]
    pytestmark = pytest.mark.django_db

    def setUp(self):
        super().setUp()
        TabSettings.set("enable_team_team_scratches", 1)
        TabSettings.set("enable_judge_judge_scratches", 1)
        TabSettings.set("cur_round", 1)
        self.generate_checkins(2)

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

    def test_team_team_scratch_prevents_pairing(self):
        """Test that team-team scratches prevent those teams from being paired"""
        # Get two teams
        teams = list(Team.objects.all()[:2])
        team_one, team_two = teams[0], teams[1]

        # Create a team-team scratch
        TeamTeamScratch.objects.create(team_one=team_one, team_two=team_two)

        # Pair the round
        cache_logic.clear_cache()
        tab_logic.pair_round()

        # Check that these two teams were not paired together
        rounds_with_both = Round.objects.filter(
            round_number=1
        ).filter(
            gov_team__in=[team_one, team_two],
            opp_team__in=[team_one, team_two]
        )

        self.assertEqual(
            rounds_with_both.count(),
            0,
            "Teams with a team-team scratch should not be paired together"
        )

    def test_judge_team_scratch_prevents_assignment(self):
        """Test that judge-team scratches prevent judge assignment to team"""
        # Pair the round first
        cache_logic.clear_cache()
        tab_logic.pair_round()

        # Get a round and create a scratch between judge and one team
        round_obj = Round.objects.filter(round_number=1).first()
        judge = Judge.objects.filter(checkin__round_number=1).first()
        team = round_obj.gov_team

        Scratch.objects.create(
            judge=judge, team=team, scratch_type=Scratch.TEAM_SCRATCH
        )

        # Clear existing judge assignments
        round_obj.judges.clear()
        round_obj.chair = None
        round_obj.save()

        # Assign judges
        assign_judges.add_judges()

        # Check that the judge was not assigned to this round
        round_obj.refresh_from_db()
        assigned_judges = list(round_obj.judges.all())
        if round_obj.chair:
            assigned_judges.append(round_obj.chair)

        self.assertNotIn(
            judge,
            assigned_judges,
            "Judge with scratch should not be assigned to team's round"
        )

    def test_judge_judge_scratch_prevents_panel_assignment(self):
        """Test that judge-judge scratches prevent judges from being on same panel"""
        # Get two judges
        judges = list(Judge.objects.filter(checkin__round_number=1)[:2])
        if len(judges) < 2:
            self.skipTest("Not enough judges for this test")

        judge_one, judge_two = judges[0], judges[1]

        # Create a judge-judge scratch
        JudgeJudgeScratch.objects.create(
            judge_one=judge_one, judge_two=judge_two
        )

        # Pair the round
        cache_logic.clear_cache()
        tab_logic.pair_round()

        # Assign judges
        assign_judges.add_judges()

        # Check that these judges are not on the same panel
        for round_obj in Round.objects.filter(round_number=1):
            panel_judges = list(round_obj.judges.all())
            if round_obj.chair:
                panel_judges.append(round_obj.chair)

            judge_ids = [j.id for j in panel_judges]

            # Both judges should not be in the same panel
            has_both = judge_one.id in judge_ids and judge_two.id in judge_ids
            self.assertFalse(
                has_both,
                f"Judges with scratch should not be on same panel. "
                f"Panel: {judge_ids}, Scratched: {judge_one.id}, {judge_two.id}"
            )

    def test_multiple_scratches_honored(self):
        """Test that multiple types of scratches are all honored"""
        # Create various scratches
        teams = list(Team.objects.all()[:4])
        judges = list(Judge.objects.filter(checkin__round_number=1)[:3])

        # Team-team scratch
        if len(teams) >= 2:
            TeamTeamScratch.objects.create(
                team_one=teams[0], team_two=teams[1]
            )

        # Judge-judge scratch
        if len(judges) >= 2:
            JudgeJudgeScratch.objects.create(
                judge_one=judges[0], judge_two=judges[1]
            )

        # Pair and assign
        cache_logic.clear_cache()
        tab_logic.pair_round()
        assign_judges.add_judges()

        # Verify team-team scratch
        if len(teams) >= 2:
            rounds_with_both_teams = Round.objects.filter(
                round_number=1,
                gov_team__in=[teams[0], teams[1]],
                opp_team__in=[teams[0], teams[1]]
            )
            self.assertEqual(rounds_with_both_teams.count(), 0)

        # Verify judge-judge scratch
        if len(judges) >= 2:
            for round_obj in Round.objects.filter(round_number=1):
                panel_judges = list(round_obj.judges.all())
                if round_obj.chair:
                    panel_judges.append(round_obj.chair)
                judge_ids = [j.id for j in panel_judges]
                has_both = judges[0].id in judge_ids and judges[1].id in judge_ids
                self.assertFalse(has_both)

    def test_impossible_team_team_scratches_raise_clean_error(self):
        """Test impossible scratch constraints fail explicitly"""
        teams = list(Team.objects.all()[:4])
        focal_team = teams[0]

        for blocked_team in teams[1:]:
            TeamTeamScratch.objects.create(
                team_one=focal_team,
                team_two=blocked_team,
            )

        with self.assertRaisesRegex(
            errors.NotEnoughTeamsError,
            "Could not satisfy all scratch constraints while pairing teams.",
        ):
            tab_logic.perfect_pairing(teams)

    def test_pullup_search_tries_alternate_team_when_default_pullup_is_scratched(self):
        TabSettings.set("cur_round", 3)
        school = School.objects.create(name="Pullup Scratch School")
        teams = [
            Team.objects.create(
                name=f"Pullup Scratch Team {idx}",
                school=school,
                seed=Team.UNSEEDED,
                tiebreaker=idx,
            )
            for idx in range(1, 7)
        ]
        bottom_team = teams[0]
        middle_teams = teams[1:5]
        top_team = teams[5]

        TeamTeamScratch.objects.create(
            team_one=top_team,
            team_two=middle_teams[-1],
        )

        pairings, pullups, bye_team = tab_logic._pair_brackets_with_pullup_search(
            brackets=[
                [bottom_team],
                list(middle_teams),
                [top_team],
            ],
            current_round=3,
            all_checked_in_teams=teams,
            middle_of_bracket=[],
            historical_pullups=[],
        )

        paired_team_ids = {
            frozenset((team_one.id, team_two.id))
            for team_one, team_two in pairings
        }
        self.assertIn(
            frozenset((top_team.id, middle_teams[-2].id)),
            paired_team_ids,
        )
        self.assertNotIn(middle_teams[-1], pullups)
        self.assertIn(middle_teams[-2], pullups)
        self.assertIsNone(bye_team)

    def test_pullup_search_tries_alternate_bye_when_lowest_team_blocks_pairing(self):
        TabSettings.set("cur_round", 2)
        school = School.objects.create(name="Alternate Bye School")
        teams = [
            Team.objects.create(
                name=f"Alternate Bye Team {idx}",
                school=school,
                seed=Team.UNSEEDED,
                tiebreaker=idx,
            )
            for idx in range(1, 6)
        ]
        bottom_teams = teams[:3]
        top_teams = teams[3:]

        TeamTeamScratch.objects.create(
            team_one=bottom_teams[0],
            team_two=bottom_teams[1],
        )

        pairings, pullups, bye_team = tab_logic._pair_brackets_with_pullup_search(
            brackets=[
                list(bottom_teams),
                list(top_teams),
            ],
            current_round=2,
            all_checked_in_teams=teams,
            middle_of_bracket=[],
            historical_pullups=[],
        )

        paired_team_ids = {
            frozenset((team_one.id, team_two.id))
            for team_one, team_two in pairings
        }
        self.assertIn(
            frozenset((bottom_teams[0].id, bottom_teams[2].id)),
            paired_team_ids,
        )
        self.assertEqual(bye_team, bottom_teams[1])
        self.assertEqual(pullups, [])

    def test_pullup_search_keeps_default_lowest_bye_choice_without_team_scratches(self):
        TabSettings.set("cur_round", 2)
        school = School.objects.create(name="Default Bye School")
        teams = [
            Team.objects.create(
                name=f"Default Bye Team {idx}",
                school=school,
                seed=Team.UNSEEDED,
                tiebreaker=idx,
            )
            for idx in range(1, 6)
        ]
        bottom_teams = teams[:3]
        top_teams = teams[3:]

        pairings, pullups, bye_team = tab_logic._pair_brackets_with_pullup_search(
            brackets=[
                list(bottom_teams),
                list(top_teams),
            ],
            current_round=2,
            all_checked_in_teams=teams,
            middle_of_bracket=[],
            historical_pullups=[],
        )

        paired_team_ids = {
            frozenset((team_one.id, team_two.id))
            for team_one, team_two in pairings
        }
        self.assertIn(
            frozenset((bottom_teams[0].id, bottom_teams[1].id)),
            paired_team_ids,
        )
        self.assertEqual(bye_team, bottom_teams[2])
        self.assertEqual(pullups, [])


@pytest.mark.django_db
class TestJudgeRoomQualityAlignment(TestCase):
    pytestmark = pytest.mark.django_db

    def setUp(self):
        super().setUp()
        TabSettings.set("cur_round", 2)
        TabSettings.set("pair_wings", 0)
        TabSettings.set("enable_room_seeding", 1)

        school = School.objects.create(name="Judge Room School")
        teams = [
            Team.objects.create(
                name=f"Quality Team {seed}",
                school=school,
                seed=seed,
            )
            for seed in (
                Team.FULL_SEED,
                Team.HALF_SEED,
                Team.FREE_SEED,
                Team.UNSEEDED,
            )
        ]

        self.top_round = Round.objects.create(
            round_number=1,
            gov_team=teams[0],
            opp_team=teams[1],
        )
        self.bottom_round = Round.objects.create(
            round_number=1,
            gov_team=teams[2],
            opp_team=teams[3],
        )

        self.top_judge = Judge.objects.create(
            name="Top Judge",
            rank=Decimal("5.00"),
        )
        self.bottom_judge = Judge.objects.create(
            name="Bottom Judge",
            rank=Decimal("1.00"),
        )
        CheckIn.objects.create(judge=self.top_judge, round_number=1)
        CheckIn.objects.create(judge=self.bottom_judge, round_number=1)

        self.top_room = Room.objects.create(
            name="Top Room",
            rank=Decimal("5.00"),
        )
        self.bottom_room = Room.objects.create(
            name="Bottom Room",
            rank=Decimal("1.00"),
        )
        RoomCheckIn.objects.create(room=self.top_room, round_number=1)
        RoomCheckIn.objects.create(room=self.bottom_room, round_number=1)

    def test_best_round_gets_best_judge_and_room(self):
        assign_judges.add_judges()
        assign_rooms.add_rooms()

        self.top_round.refresh_from_db()
        self.bottom_round.refresh_from_db()

        self.assertEqual(
            self.top_round.chair,
            self.top_judge,
            "The strongest judge should chair the strongest round",
        )
        self.assertEqual(
            self.top_round.room,
            self.top_room,
            "The strongest round should receive the strongest room",
        )
        self.assertEqual(
            self.bottom_round.chair,
            self.bottom_judge,
            "The weaker judge should be assigned to the weaker round",
        )
        self.assertEqual(
            self.bottom_round.room,
            self.bottom_room,
            "The weaker round should receive the weaker room",
        )
