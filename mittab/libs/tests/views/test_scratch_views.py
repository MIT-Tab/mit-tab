import copy

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from mittab.apps.tab.models import (
    Judge,
    School,
    Team,
    Scratch,
    JudgeJudgeScratch,
    TeamTeamScratch,
)

TEST_WEBPACK_LOADER = copy.deepcopy(settings.WEBPACK_LOADER)
TEST_WEBPACK_LOADER["DEFAULT"]["LOADER_CLASS"] = (
    "webpack_loader.loaders.FakeWebpackLoader"
)


@pytest.mark.django_db(transaction=True)
@override_settings(WEBPACK_LOADER=TEST_WEBPACK_LOADER)
class TestScratchViews(TestCase):
    """Test the scratch views functionality"""

    fixtures = ["testing_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="testuser",
            password="testpass123",
            email="test@test.com",
        )
        self.client.login(username="testuser", password="testpass123")

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

    def test_add_scratch_view_honors_requested_count(self):
        """Test the add scratch view builds the requested number of forms"""
        response = self.client.get(
            reverse("add_scratch"),
            {"team_id": Team.objects.first().id, "count": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["forms_by_type"]["judge_team"]), 2)

    def test_add_multiple_judge_team_scratches(self):
        """Test adding multiple judge-team scratches in one submission"""
        judges = list(Judge.objects.all()[:2])
        teams = list(Team.objects.all()[:2])

        response = self.client.post(
            reverse("add_scratch"),
            {
                "form_type": "judge_team",
                "count": 2,
                "judge_team_0-judge": judges[0].id,
                "judge_team_0-team": teams[0].id,
                "judge_team_0-scratch_type": Scratch.TEAM_SCRATCH,
                "judge_team_1-judge": judges[1].id,
                "judge_team_1-team": teams[1].id,
                "judge_team_1-scratch_type": Scratch.TAB_SCRATCH,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Scratch.objects.filter(judge=judges[0], team=teams[0]).exists())
        self.assertTrue(
            Scratch.objects.filter(
                judge=judges[1],
                team=teams[1],
                scratch_type=Scratch.TAB_SCRATCH,
            ).exists()
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

        response = self.client.get(reverse("view_scratches_judge", args=[judge.id]))
        self.assertEqual(response.status_code, 200)

    def test_view_scratches_for_team(self):
        """Test viewing scratches for a specific team"""
        judge = Judge.objects.first()
        team = Team.objects.first()
        Scratch.objects.create(
            judge=judge, team=team, scratch_type=Scratch.TEAM_SCRATCH
        )

        response = self.client.get(reverse("view_scratches_team", args=[team.id]))
        self.assertEqual(response.status_code, 200)

    def test_view_scratches_for_team_saves_edits(self):
        """Test editing an existing scratch from the object page"""
        school = School.objects.create(name="Scratch View School")
        team = Team.objects.create(
            name="Scratch View Team",
            school=school,
            seed=Team.UNSEEDED,
        )
        judges = [
            Judge.objects.create(name="Scratch View Judge 1", rank=1),
            Judge.objects.create(name="Scratch View Judge 2", rank=2),
        ]
        scratch = Scratch.objects.create(
            judge=judges[0], team=team, scratch_type=Scratch.TEAM_SCRATCH
        )

        response = self.client.post(
            reverse("view_scratches_team", args=[team.id]),
            {
                "1-judge": judges[1].id,
                "1-team": team.id,
                "1-scratch_type": Scratch.TAB_SCRATCH,
            },
        )

        self.assertEqual(response.status_code, 302)
        scratch.refresh_from_db()
        self.assertEqual(scratch.judge_id, judges[1].id)
        self.assertEqual(scratch.scratch_type, Scratch.TAB_SCRATCH)

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

    def test_scratch_detail_saves_edits(self):
        """Test editing a scratch from the detail page"""
        judge = Judge.objects.first()
        team = Team.objects.first()
        scratch = Scratch.objects.create(
            judge=judge, team=team, scratch_type=Scratch.TEAM_SCRATCH
        )

        response = self.client.post(
            reverse("scratch_detail", args=["judge-team", scratch.id]),
            {
                "judge": judge.id,
                "team": team.id,
                "scratch_type": Scratch.TAB_SCRATCH,
            },
        )

        self.assertEqual(response.status_code, 302)
        scratch.refresh_from_db()
        self.assertEqual(scratch.scratch_type, Scratch.TAB_SCRATCH)

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

    def test_team_detail_uses_current_scratch_link(self):
        """Test the team detail page links to the current scratch route"""
        team = Team.objects.first()

        response = self.client.get(reverse("view_team", args=[team.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("view_scratches_team", args=[team.id]),
        )
