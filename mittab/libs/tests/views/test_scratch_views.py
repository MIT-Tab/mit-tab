import pytest
from django.test import TestCase, Client
from django.urls import reverse

from mittab.apps.tab.models import (
    Judge,
    Team,
    Scratch,
    JudgeJudgeScratch,
    TeamTeamScratch,
)


@pytest.mark.django_db(transaction=True)
class TestScratchViews(TestCase):
    """Test the scratch views functionality"""

    fixtures = ["testing_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username="admin", password="admin")

    def test_add_scratch_view_loads(self):
        """Test that the add scratch view loads successfully"""
        response = self.client.get(reverse("add_scratch"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("forms_by_type", response.context)
        self.assertIn("judge_team", response.context["forms_by_type"])
        self.assertIn("judge_judge", response.context["forms_by_type"])
        self.assertIn("team_team", response.context["forms_by_type"])

    def test_add_judge_team_scratch(self):
        """Test adding a judge-team scratch"""
        judge = Judge.objects.first()
        team = Team.objects.first()
        
        response = self.client.post(
            reverse("add_scratch"),
            {
                "form_type": "judge_team",
                "judge_team_0-judge": judge.id,
                "judge_team_0-team": team.id,
                "judge_team_0-scratch_type": Scratch.TEAM_SCRATCH,
            },
        )
        
        # Should redirect on success
        self.assertEqual(response.status_code, 302)
        
        # Verify scratch was created
        self.assertTrue(
            Scratch.objects.filter(judge=judge, team=team).exists()
        )

    def test_add_judge_judge_scratch(self):
        """Test adding a judge-judge scratch"""
        judges = list(Judge.objects.all()[:2])
        judge_one, judge_two = judges[0], judges[1]
        
        response = self.client.post(
            reverse("add_scratch"),
            {
                "form_type": "judge_judge",
                "judge_judge_0-judge_one": judge_one.id,
                "judge_judge_0-judge_two": judge_two.id,
            },
        )
        
        # Should redirect on success
        self.assertEqual(response.status_code, 302)
        
        # Verify scratch was created
        self.assertTrue(
            JudgeJudgeScratch.objects.filter(
                judge_one=min(judge_one, judge_two, key=lambda j: j.id),
                judge_two=max(judge_one, judge_two, key=lambda j: j.id),
            ).exists()
        )

    def test_add_team_team_scratch(self):
        """Test adding a team-team scratch"""
        teams = list(Team.objects.all()[:2])
        team_one, team_two = teams[0], teams[1]
        
        response = self.client.post(
            reverse("add_scratch"),
            {
                "form_type": "team_team",
                "team_team_0-team_one": team_one.id,
                "team_team_0-team_two": team_two.id,
            },
        )
        
        # Should redirect on success
        self.assertEqual(response.status_code, 302)
        
        # Verify scratch was created
        self.assertTrue(
            TeamTeamScratch.objects.filter(
                team_one=min(team_one, team_two, key=lambda t: t.id),
                team_two=max(team_one, team_two, key=lambda t: t.id),
            ).exists()
        )

    def test_view_scratches(self):
        """Test viewing all scratches"""
        # Create some scratches
        judge = Judge.objects.first()
        team = Team.objects.first()
        Scratch.objects.create(
            judge=judge, team=team, scratch_type=Scratch.TEAM_SCRATCH
        )
        
        response = self.client.get(reverse("view_scratches"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("item_list", response.context)

    def test_view_scratches_for_judge(self):
        """Test viewing scratches for a specific judge"""
        judge = Judge.objects.first()
        team = Team.objects.first()
        Scratch.objects.create(
            judge=judge, team=team, scratch_type=Scratch.TEAM_SCRATCH
        )
        
        response = self.client.get(f"/scratches/judge/{judge.id}/")
        self.assertEqual(response.status_code, 200)

    def test_view_scratches_for_team(self):
        """Test viewing scratches for a specific team"""
        judge = Judge.objects.first()
        team = Team.objects.first()
        Scratch.objects.create(
            judge=judge, team=team, scratch_type=Scratch.TEAM_SCRATCH
        )
        
        response = self.client.get(f"/scratches/team/{team.id}/")
        self.assertEqual(response.status_code, 200)

    def test_scratch_detail_judge_team(self):
        """Test viewing a single judge-team scratch detail"""
        judge = Judge.objects.first()
        team = Team.objects.first()
        scratch = Scratch.objects.create(
            judge=judge, team=team, scratch_type=Scratch.TAB_SCRATCH
        )
        
        response = self.client.get(
            reverse("scratch_detail", args=["judge-team", scratch.id])
        )
        self.assertEqual(response.status_code, 200)

    def test_scratch_detail_judge_judge(self):
        """Test viewing a single judge-judge scratch detail"""
        judges = list(Judge.objects.all()[:2])
        scratch = JudgeJudgeScratch.objects.create(
            judge_one=judges[0], judge_two=judges[1]
        )
        
        response = self.client.get(
            reverse("scratch_detail", args=["judge-judge", scratch.id])
        )
        self.assertEqual(response.status_code, 200)

    def test_scratch_detail_team_team(self):
        """Test viewing a single team-team scratch detail"""
        teams = list(Team.objects.all()[:2])
        scratch = TeamTeamScratch.objects.create(
            team_one=teams[0], team_two=teams[1]
        )
        
        response = self.client.get(
            reverse("scratch_detail", args=["team-team", scratch.id])
        )
        self.assertEqual(response.status_code, 200)

    def test_scratch_delete_judge_team(self):
        """Test deleting a judge-team scratch"""
        judge = Judge.objects.first()
        team = Team.objects.first()
        scratch = Scratch.objects.create(
            judge=judge, team=team, scratch_type=Scratch.TEAM_SCRATCH
        )
        
        response = self.client.get(
            reverse("scratch_delete", args=["judge-team", scratch.id])
        )
        
        # Should redirect on success
        self.assertEqual(response.status_code, 302)
        
        # Verify scratch was deleted
        self.assertFalse(Scratch.objects.filter(id=scratch.id).exists())

    def test_scratch_delete_judge_judge(self):
        """Test deleting a judge-judge scratch"""
        judges = list(Judge.objects.all()[:2])
        scratch = JudgeJudgeScratch.objects.create(
            judge_one=judges[0], judge_two=judges[1]
        )
        
        response = self.client.get(
            reverse("scratch_delete", args=["judge-judge", scratch.id])
        )
        
        # Should redirect on success
        self.assertEqual(response.status_code, 302)
        
        # Verify scratch was deleted
        self.assertFalse(
            JudgeJudgeScratch.objects.filter(id=scratch.id).exists()
        )

    def test_scratch_delete_team_team(self):
        """Test deleting a team-team scratch"""
        teams = list(Team.objects.all()[:2])
        scratch = TeamTeamScratch.objects.create(
            team_one=teams[0], team_two=teams[1]
        )
        
        response = self.client.get(
            reverse("scratch_delete", args=["team-team", scratch.id])
        )
        
        # Should redirect on success
        self.assertEqual(response.status_code, 302)
        
        # Verify scratch was deleted
        self.assertFalse(
            TeamTeamScratch.objects.filter(id=scratch.id).exists()
        )

    def test_duplicate_scratch_error(self):
        """Test that adding a duplicate scratch shows an error"""
        judge = Judge.objects.first()
        team = Team.objects.first()
        
        # Create first scratch
        Scratch.objects.create(
            judge=judge, team=team, scratch_type=Scratch.TEAM_SCRATCH
        )
        
        # Try to create duplicate
        response = self.client.post(
            reverse("add_scratch"),
            {
                "form_type": "judge_team",
                "judge_team_0-judge": judge.id,
                "judge_team_0-team": team.id,
                "judge_team_0-scratch_type": Scratch.TEAM_SCRATCH,
            },
        )
        
        # Should not redirect (stays on page with error)
        self.assertEqual(response.status_code, 200)
        # Should still only have one scratch
        self.assertEqual(
            Scratch.objects.filter(judge=judge, team=team).count(), 1
        )
