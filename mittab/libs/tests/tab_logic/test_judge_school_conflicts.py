from decimal import Decimal

from django.test import TestCase
import pytest

from mittab.apps.tab.models import (
    BreakingTeam,
    CheckIn,
    Judge,
    Outround,
    Room,
    RoomCheckIn,
    Round,
    School,
    Scratch,
    TabSettings,
    Team,
)
from mittab.libs import assign_judges, tab_logic
from mittab.libs.assign_judges import judge_conflict
from mittab.libs.tests.helpers import (
    build_judge_pool,
    build_room_pool,
    clear_all_scratches,
)


@pytest.mark.django_db
class TestMinimalSchoolConflictScenarios(TestCase):
    pytestmark = pytest.mark.django_db

    def make_team(self, name, school, hybrid_school=None):
        return Team.objects.create(
            name=name,
            school=school,
            hybrid_school=hybrid_school,
            seed=Team.FULL_SEED,
        )

    def make_judge(self, name, rank=Decimal("5.00")):
        return Judge.objects.create(name=name, rank=rank)

    def test_judge_conflict_detects_primary_and_hybrid_schools(self):
        school_primary = School.objects.create(name="Primary U")
        school_other = School.objects.create(name="Other U")
        hybrid_school = School.objects.create(name="Hybrid U")

        gov_team = self.make_team("Gov Team", school_primary)
        opp_team = self.make_team("Opp Team", school_other, hybrid_school=hybrid_school)

        primary_judge = self.make_judge("Primary Judge", Decimal("4.10"))
        primary_judge.schools.add(school_primary)

        hybrid_judge = self.make_judge("Hybrid Judge", Decimal("4.20"))
        hybrid_judge.schools.add(hybrid_school)

        neutral_judge = self.make_judge("Neutral Judge", Decimal("4.30"))

        self.assertTrue(
            judge_conflict(primary_judge, gov_team, opp_team),
            "Primary school affiliation should be treated as a scratch",
        )
        self.assertTrue(
            judge_conflict(hybrid_judge, gov_team, opp_team),
            "Hybrid school affiliation should be treated as a scratch",
        )
        self.assertFalse(
            judge_conflict(neutral_judge, gov_team, opp_team),
            "Judges without matching schools should be eligible",
        )

    def test_add_judges_skips_school_conflicts_in_inrounds(self):
        TabSettings.set("cur_round", 2)
        TabSettings.set("pair_wings", 0)
        TabSettings.set("allow_rejudges", 0)

        school_primary = School.objects.create(name="Primary Inround")
        school_other = School.objects.create(name="Other Inround")

        gov_team = self.make_team("Inround Gov", school_primary)
        opp_team = self.make_team("Inround Opp", school_other)

        round_obj = Round.objects.create(
            round_number=1,
            gov_team=gov_team,
            opp_team=opp_team,
        )

        conflict_judge = self.make_judge("Conflict Inround Judge", Decimal("4.50"))
        conflict_judge.schools.add(school_primary)
        neutral_judge = self.make_judge("Neutral Inround Judge", Decimal("4.60"))

        CheckIn.objects.create(judge=conflict_judge, round_number=1)
        CheckIn.objects.create(judge=neutral_judge, round_number=1)

        assign_judges.add_judges()
        round_obj.refresh_from_db()

        self.assertEqual(
            round_obj.chair,
            neutral_judge,
            "Judges sharing a school's affiliation should never be assigned",
        )
        self.assertNotIn(
            conflict_judge,
            round_obj.judges.all(),
            "Conflicted judges must not be added to the panel",
        )

    def test_add_outround_judges_skips_school_conflicts(self):
        TabSettings.set("var_panel_size", 1)

        school_primary = School.objects.create(name="Primary Outround")
        school_other = School.objects.create(name="Other Outround")

        gov_team = self.make_team("Gov Outround", school_primary)
        opp_team = self.make_team("Opp Outround", school_other)

        BreakingTeam.objects.create(
            team=gov_team,
            seed=1,
            effective_seed=1,
            type_of_team=BreakingTeam.VARSITY,
        )
        BreakingTeam.objects.create(
            team=opp_team,
            seed=2,
            effective_seed=2,
            type_of_team=BreakingTeam.VARSITY,
        )

        room = Room.objects.create(name="Outround Room", rank=Decimal("5.00"))
        outround = Outround.objects.create(
            num_teams=2,
            type_of_round=Outround.VARSITY,
            gov_team=gov_team,
            opp_team=opp_team,
            room=room,
        )

        conflict_judge = self.make_judge("Conflict Outround Judge", Decimal("4.50"))
        conflict_judge.schools.add(school_primary)
        neutral_judge = self.make_judge("Neutral Outround Judge", Decimal("4.60"))

        CheckIn.objects.create(judge=conflict_judge, round_number=0)
        CheckIn.objects.create(judge=neutral_judge, round_number=0)

        assign_judges.add_outround_judges(round_type=Outround.VARSITY)
        outround.refresh_from_db()

        self.assertEqual(
            outround.chair,
            neutral_judge,
            "Outround assignments must skip judges with school conflicts",
        )
        self.assertNotIn(
            conflict_judge,
            outround.judges.all(),
            "Conflicted judges should not be added to any outround panel",
        )


@pytest.mark.django_db
class TestDynamicSchoolConflicts(TestCase):
    fixtures = ["testing_db"]
    pytestmark = pytest.mark.django_db

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        clear_all_scratches()

    def setUp(self):
        super().setUp()
        TabSettings.set("cur_round", 1)
        clear_all_scratches()

    def prepare_tournament_round(self, num_teams=20, num_judges=None):
        Team.objects.all().update(checked_in=False)

        teams = list(Team.objects.order_by("id")[:num_teams])
        team_ids = [team.id for team in teams]
        Team.objects.filter(id__in=team_ids).update(checked_in=True)

        num_needed = len(teams) // 2
        if num_judges is None:
            num_judges = num_needed + 5

        schools = list(School.objects.order_by("id")[:3])

        build_room_pool(num_needed + 5, round_numbers=(1,))
        judges = build_judge_pool(num_judges, checkin_rounds=(0, 1))
        return teams, judges, schools, num_needed

    def pair_and_assign_round(self, round_number=1, before_assign=None):
        tab_logic.pair_round()
        if callable(before_assign):
            before_assign()
        TabSettings.set("cur_round", round_number + 1)
        assign_judges.add_judges()
        return list(
            Round.objects.filter(round_number=round_number)
            .select_related(
                "gov_team__school",
                "gov_team__hybrid_school",
                "opp_team__school",
                "opp_team__hybrid_school",
            )
            .prefetch_related("chair__schools", "chair__scratches")
        )

    def find_assignment_conflicts(self,
                                  rounds,
                                  include_hybrid=False,
                                  include_scratches=False):
        conflicts = []
        for round_obj in rounds:
            chair = round_obj.chair
            if not chair:
                continue
            chair_school_ids = set(
                chair.schools.values_list("id", flat=True)
            )
            chair_scratch_ids = (
                set(chair.scratches.values_list("team_id", flat=True))
                if include_scratches
                else set()
            )
            for team in (round_obj.gov_team, round_obj.opp_team):
                if team.school_id in chair_school_ids:
                    conflicts.append(
                        f"Judge {chair.name} assigned to {team.name} "
                        f"from school {team.school.name}"
                    )
                if include_hybrid and team.hybrid_school_id and \
                        team.hybrid_school_id in chair_school_ids:
                    conflicts.append(
                        f"Judge {chair.name} assigned to {team.name} "
                        f"with hybrid school {team.hybrid_school.name}"
                    )
                if include_scratches and team.id in chair_scratch_ids:
                    conflicts.append(
                        f"Judge {chair.name} assigned to {team.name} "
                        "despite explicit scratch"
                    )
        return conflicts

    def count_hybrid_comparisons(self, rounds):
        return sum(
            1
            for round_obj in rounds
            if round_obj.chair
            for team in (round_obj.gov_team, round_obj.opp_team)
            if team.hybrid_school_id
        )

    def test_judge_assignment_honors_primary_school_conflicts(self):
        teams, judges, schools, _ = self.prepare_tournament_round(num_teams=20, num_judges=15)

        # Set up school affiliations for judges
        # First 3 judges affiliated with school 0
        for judge in judges[0:3]:
            judge.schools.add(schools[0])

        # Next 3 judges affiliated with school 1
        for judge in judges[3:6]:
            judge.schools.add(schools[1])

        # Next 3 judges affiliated with school 2
        for judge in judges[6:9]:
            judge.schools.add(schools[2])

        # Remaining judges have no school affiliations

        # Assign teams to schools in a pattern to ensure conflicts will exist
        for i, team in enumerate(teams[:12]):
            team.school = schools[i % 3]
            team.save()

        rounds = self.pair_and_assign_round()
        self.assertGreater(len(rounds), 0, "Rounds should have been created")

        conflicts_found = self.find_assignment_conflicts(
            rounds,
            include_hybrid=True,
        )
        self.assertFalse(
            conflicts_found,
            "Found school conflicts:\n" + "\n".join(conflicts_found),
        )

        rounds_with_chairs = sum(1 for round_obj in rounds if round_obj.chair)
        self.assertGreater(
            rounds_with_chairs,
            0,
            "At least some rounds should have chair judges assigned",
        )

    def test_hybrid_school_conflicts_are_respected(self):
        teams, judges, schools, _ = self.prepare_tournament_round(num_teams=16, num_judges=12)

        # Create a fourth school for hybrid affiliations
        school_hybrid = School.objects.create(name="Hybrid Test School")

        # Set up judge affiliations
        judges[0].schools.add(schools[0])  # Affiliated with school 0
        judges[1].schools.add(schools[1])  # Affiliated with school 1
        judges[2].schools.add(school_hybrid)  # Affiliated with hybrid school
        # Remaining judges have no affiliations

        paired_hybrids = []

        def assign_hybrids_to_paired_teams():
            paired_rounds = list(
                Round.objects.filter(round_number=1)
                .select_related("gov_team", "opp_team")
                .order_by("id")
            )
            for round_obj in paired_rounds:
                for team in (round_obj.gov_team, round_obj.opp_team):
                    if len(paired_hybrids) >= 4:
                        return
                    team.hybrid_school = school_hybrid
                    team.save(update_fields=["hybrid_school"])
                    paired_hybrids.append(team.id)

        rounds = self.pair_and_assign_round(before_assign=assign_hybrids_to_paired_teams)
        conflicts = self.find_assignment_conflicts(
            rounds,
            include_hybrid=True,
        )
        self.assertFalse(
            conflicts,
            "Hybrid conflicts detected:\n" + "\n".join(conflicts),
        )

        self.assertGreater(
            self.count_hybrid_comparisons(rounds),
            0,
            "Should have validated at least some hybrid school conflicts",
        )

    def test_explicit_scratches_still_enforced_alongside_school_conflicts(self):
        teams, judges, schools, _ = self.prepare_tournament_round(num_teams=16, num_judges=12)

        # Set up school affiliations
        judges[0].schools.add(schools[0])
        judges[1].schools.add(schools[1])

        # Set up teams
        teams[0].school = schools[0]  # Will conflict with judges[0] via school
        teams[0].save()

        teams[1].school = schools[1]  # Will conflict with judges[1] via school
        teams[1].save()

        teams[2].school = schools[2]  # No school conflict
        teams[2].save()

        # Create explicit scratches for teams[2] (which has no school conflict)
        Scratch.objects.create(
            judge=judges[0],
            team=teams[2],
            scratch_type=Scratch.TEAM_SCRATCH
        )
        Scratch.objects.create(
            judge=judges[1],
            team=teams[2],
            scratch_type=Scratch.TAB_SCRATCH
        )

        # Count scratches before pairing
        initial_scratch_count = Scratch.objects.count()

        # Reload judges with prefetched relations
        judges = list(Judge.objects.prefetch_related("scratches", "schools", "judges").all())

        # Verify judge_conflict detects both types of conflicts
        # School conflict
        self.assertTrue(
            judge_conflict(judges[0], teams[0], teams[2]),
            "Should detect school conflict with teams[0]"
        )

        # Explicit scratch conflict
        self.assertTrue(
            judge_conflict(judges[0], teams[2], teams[3]),
            "Should detect explicit scratch with teams[2]"
        )

        # No conflict
        self.assertFalse(
            judge_conflict(judges[2], teams[3], teams[4]),
            "Should not detect conflict when no scratches or school conflicts exist"
        )

        rounds = self.pair_and_assign_round()

        # Verify no new automatic scratches were created
        final_scratch_count = Scratch.objects.count()
        self.assertEqual(
            initial_scratch_count,
            final_scratch_count,
            "No new scratch records should be created automatically for school conflicts"
        )

        conflicts = self.find_assignment_conflicts(
            rounds,
            include_scratches=True,
        )
        self.assertFalse(
            conflicts,
            "Assignments should respect school and scratch conflicts:\n"
            + "\n".join(conflicts),
        )

    def test_allow_rejudges_setting_with_school_conflicts(self):
        # Set up a smaller tournament for multi-round testing
        teams, judges, schools, _ = self.prepare_tournament_round(num_teams=12, num_judges=10)

        # Set up school affiliations
        judges[0].schools.add(schools[0])
        judges[1].schools.add(schools[1])

        # Set up teams
        teams[0].school = schools[0]
        teams[0].save()
        teams[1].school = schools[1]
        teams[1].save()
        teams[2].school = schools[2]
        teams[2].save()
        teams[3].school = schools[2]
        teams[3].save()

        # Test with allow_rejudges = False (default)
        TabSettings.set("allow_rejudges", False)

        # Reload judges with prefetched relations
        judges_prefetched = list(Judge.objects.prefetch_related("scratches", "schools", "judges").all())

        # School conflicts should be detected
        self.assertTrue(
            judge_conflict(judges_prefetched[0], teams[0], teams[1], allow_rejudges=False),
            "School conflict should be detected with allow_rejudges=False"
        )

        # Now test with allow_rejudges = True
        TabSettings.set("allow_rejudges", True)

        # School conflicts should STILL be detected
        self.assertTrue(
            judge_conflict(judges_prefetched[0], teams[0], teams[1], allow_rejudges=True),
            "School conflict should STILL be detected with allow_rejudges=True"
        )

        # Create a previous round where judges[2] judged teams[2]
        Round.objects.create(
            round_number=0,
            gov_team=teams[2],
            opp_team=teams[3],
            chair=judges_prefetched[2],
            victor=Round.GOV
        ).judges.add(judges_prefetched[2])

        # Reload judges to pick up the round relation
        judges_prefetched = list(Judge.objects.prefetch_related("scratches", "schools", "judges").all())

        # With allow_rejudges=False, previous judging should cause conflict
        self.assertTrue(
            judge_conflict(judges_prefetched[2], teams[2], teams[3], allow_rejudges=False),
            "Previous judging should conflict when allow_rejudges=False"
        )

        # With allow_rejudges=True, previous judging should NOT cause conflict (no school conflict)
        self.assertFalse(
            judge_conflict(judges_prefetched[2], teams[2], teams[3], allow_rejudges=True),
            "Previous judging should not conflict when allow_rejudges=True and no school conflict"
        )

        # But if there IS a school conflict, it should still be enforced
        judges_prefetched[2].schools.add(schools[2])
        judges_prefetched = list(Judge.objects.prefetch_related("scratches", "schools", "judges").all())

        self.assertTrue(
            judge_conflict(judges_prefetched[2], teams[2], teams[3], allow_rejudges=True),
            "School conflict should be enforced even with allow_rejudges=True"
        )

        # Run a full round to ensure the integration works end-to-end
        TabSettings.set("cur_round", 1)
        TabSettings.set("allow_rejudges", True)

        # Add more checkins for round 1/2
        for judge in Judge.objects.all():
            CheckIn.objects.get_or_create(judge=judge, round_number=2)
        for room in Room.objects.all():
            RoomCheckIn.objects.get_or_create(room=room, round_number=2)

        rounds = self.pair_and_assign_round()
        conflicts = self.find_assignment_conflicts(rounds)
        self.assertFalse(
            conflicts,
            "School conflicts must be enforced even with allow_rejudges=True:\n"
            + "\n".join(conflicts),
        )
