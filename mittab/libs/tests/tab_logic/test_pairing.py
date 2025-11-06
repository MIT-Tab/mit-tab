from django.db import transaction
from django.test import TestCase
import pytest

from mittab.apps.tab.models import (
    CheckIn,
    Judge,
    JudgeJudgeScratch,
    Room,
    RoomCheckIn,
    Round,
    School,
    Scratch,
    TabSettings,
    Team,
    TeamTeamScratch,
)
from mittab.libs import assign_judges, assign_rooms
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
class TestScratchHonoring(TestCase):
    """Test that scratches are properly honored during pairing"""

    fixtures = ["testing_db"]
    pytestmark = pytest.mark.django_db

    def setUp(self):
        super().setUp()
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
        judge = Judge.objects.filter(checkins__round_number=1).first()
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
        judges = list(Judge.objects.filter(checkins__round_number=1)[:2])
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
        judges = list(Judge.objects.filter(checkins__round_number=1)[:3])

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
