from django.test import TestCase
import pytest

from mittab.apps.tab.models import (
    CheckIn,
    Judge,
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


@pytest.mark.django_db
class TestDynamicSchoolConflicts(TestCase):
    fixtures = ["testing_db"]
    pytestmark = pytest.mark.django_db

    def setUp(self):
        super().setUp()
        TabSettings.set("cur_round", 1)
        # Clear any existing scratches to start fresh
        Scratch.objects.all().delete()

    def prepare_tournament_round(self, num_teams=20, num_judges=None):
        # First, uncheck ALL teams to start fresh
        Team.objects.all().update(checked_in=False)
        
        # Then check in only the teams we want
        teams = list(Team.objects.all()[:num_teams])
        for team in teams:
            team.checked_in = True
            team.save()
        
        # Calculate how many judges/rooms we need
        num_needed = Team.objects.filter(checked_in=True).count() // 2
        if num_judges is None:
            num_judges = num_needed + 5
        
        # Get schools for assignment
        schools = list(School.objects.all()[:3])
        
        # Set up rooms - validate_round_data checks for rooms at round_to_check (cur_round)
        # So we need rooms for round 1
        Room.objects.all().delete()
        RoomCheckIn.objects.all().delete()
        for i in range(num_needed + 5):
            room = Room.objects.create(name=f"Test Room {i}", rank=5.0)
            RoomCheckIn.objects.create(room=room, round_number=1)
        
        # Clear and create judges
        Judge.objects.all().delete()
        CheckIn.objects.all().delete()
        
        judges = []
        for i in range(num_judges):
            judge = Judge.objects.create(name=f"Test Judge {i}", rank=5.0 - (i * 0.05))
            CheckIn.objects.create(judge=judge, round_number=0)  # For add_judges()
            CheckIn.objects.create(judge=judge, round_number=1)  # For validate_round_data()
            judges.append(judge)
        
        return teams, judges, schools, num_needed

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
        
        # Pair the round
        tab_logic.pair_round()
        TabSettings.set("cur_round", 2)
        
        # Assign judges
        assign_judges.add_judges()
        
        # Validate all assignments
        rounds = Round.objects.filter(round_number=1)
        self.assertGreater(rounds.count(), 0, "Rounds should have been created")
        
        conflicts_found = []
        rounds_with_chairs = 0
        
        for round_obj in rounds:
            if round_obj.chair:
                rounds_with_chairs += 1
                chair_schools = set(round_obj.chair.schools.all())
                
                # Check government team
                if round_obj.gov_team.school in chair_schools:
                    conflicts_found.append(
                        f"Judge {round_obj.chair.name} assigned to gov team "
                        f"{round_obj.gov_team.name} from same school {round_obj.gov_team.school.name}"
                    )
                
                # Check opposition team
                if round_obj.opp_team.school in chair_schools:
                    conflicts_found.append(
                        f"Judge {round_obj.chair.name} assigned to opp team "
                        f"{round_obj.opp_team.name} from same school {round_obj.opp_team.school.name}"
                    )
                
                # Check hybrid schools if they exist
                if round_obj.gov_team.hybrid_school and round_obj.gov_team.hybrid_school in chair_schools:
                    conflicts_found.append(
                        f"Judge {round_obj.chair.name} assigned to gov team "
                        f"{round_obj.gov_team.name} with conflicting hybrid school"
                    )
                if round_obj.opp_team.hybrid_school and round_obj.opp_team.hybrid_school in chair_schools:
                    conflicts_found.append(
                        f"Judge {round_obj.chair.name} assigned to opp team "
                        f"{round_obj.opp_team.name} with conflicting hybrid school"
                    )
        
        # Assert no conflicts were found
        self.assertEqual(
            len(conflicts_found), 0,
            f"Found {len(conflicts_found)} school conflicts:\n" + "\n".join(conflicts_found)
        )
        
        # Verify that judges were actually assigned
        self.assertGreater(
            rounds_with_chairs, 0,
            "At least some rounds should have chair judges assigned"
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
        
        # Set up teams with various school configurations
        teams[0].school = schools[0]
        teams[0].hybrid_school = None
        teams[0].save()
        
        teams[1].school = schools[1]
        teams[1].hybrid_school = school_hybrid  # This team has a hybrid school
        teams[1].save()
        
        teams[2].school = schools[2]
        teams[2].hybrid_school = school_hybrid  # Another team with hybrid school
        teams[2].save()
        
        teams[3].school = schools[2]
        teams[3].hybrid_school = None
        teams[3].save()
        
        # Pair and assign
        tab_logic.pair_round()
        TabSettings.set("cur_round", 2)
        assign_judges.add_judges()
        
        # Validate assignments
        rounds = Round.objects.filter(round_number=1)
        hybrid_conflict_checks = 0
        
        for round_obj in rounds:
            if round_obj.chair:
                chair_schools = set(round_obj.chair.schools.all())
                
                # Check both teams for both primary and hybrid school conflicts
                for team in [round_obj.gov_team, round_obj.opp_team]:
                    # Primary school conflict
                    self.assertNotIn(
                        team.school,
                        chair_schools,
                        f"Judge {round_obj.chair.name} should not judge team "
                        f"{team.name} from their primary school {team.school.name}"
                    )
                    
                    # Hybrid school conflict
                    if team.hybrid_school:
                        hybrid_conflict_checks += 1
                        self.assertNotIn(
                            team.hybrid_school,
                            chair_schools,
                            f"Judge {round_obj.chair.name} should not judge team "
                            f"{team.name} from their hybrid school {team.hybrid_school.name}"
                        )
        
        # Verify we actually checked some hybrid school scenarios
        self.assertGreater(
            hybrid_conflict_checks, 0,
            "Should have validated at least some hybrid school conflicts"
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
        
        # Pair and assign
        tab_logic.pair_round()
        TabSettings.set("cur_round", 2)
        assign_judges.add_judges()
        
        # Verify no new automatic scratches were created
        final_scratch_count = Scratch.objects.count()
        self.assertEqual(
            initial_scratch_count,
            final_scratch_count,
            "No new scratch records should be created automatically for school conflicts"
        )
        
        # Validate assignments respect both conflicts
        rounds = Round.objects.filter(round_number=1)
        
        for round_obj in rounds:
            if round_obj.chair:
                chair_schools = set(round_obj.chair.schools.all())
                chair_scratches = set(s.team_id for s in round_obj.chair.scratches.all())
                
                for team in [round_obj.gov_team, round_obj.opp_team]:
                    # No school conflicts
                    self.assertNotIn(
                        team.school,
                        chair_schools,
                        f"School conflict: Judge {round_obj.chair.name} should not "
                        f"judge {team.name} from school {team.school.name}"
                    )
                    
                    # No scratch conflicts
                    self.assertNotIn(
                        team.id,
                        chair_scratches,
                        f"Scratch conflict: Judge {round_obj.chair.name} should not "
                        f"judge {team.name} due to explicit scratch"
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
        
        tab_logic.pair_round()
        TabSettings.set("cur_round", 2)
        assign_judges.add_judges()
        
        # Verify school conflicts are still enforced
        rounds = Round.objects.filter(round_number=1)
        for round_obj in rounds:
            if round_obj.chair:
                chair_schools = set(round_obj.chair.schools.all())
                for team in [round_obj.gov_team, round_obj.opp_team]:
                    self.assertNotIn(
                        team.school,
                        chair_schools,
                        f"School conflicts must be enforced even with allow_rejudges=True"
                    )
