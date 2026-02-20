import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.tab.forms import RankingGroupForm
from mittab.apps.tab.models import Debater, RankingGroup, TabSettings, Team


@pytest.mark.django_db(transaction=True)
class TestRankingGroups(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="testuser",
            password="testpass123",
            email="test@test.com",
        )
        self.client.login(username="testuser", password="testpass123")
        TabSettings.set("cur_round", 2)
        self.team = Team.objects.first()
        self.debater = Debater.objects.first()

    def test_ranking_group_form_commit_false(self):
        form = RankingGroupForm(
            data={
                "name": "Special Group",
                "teams": [self.team.id],
                "debaters": [self.debater.id],
            }
        )
        self.assertTrue(form.is_valid())

        ranking_group = form.save(commit=False)
        self.assertIsNone(ranking_group.pk)

        ranking_group.save()
        form.save_m2m()

        self.assertEqual(list(ranking_group.teams.all()), [self.team])
        self.assertEqual(list(ranking_group.debaters.all()), [self.debater])

    def test_rank_teams_includes_ranking_group_section(self):
        ranking_group = RankingGroup.objects.create(name="Top Seeds")
        ranking_group.teams.add(self.team)

        response = self.client.get(reverse("rank_teams"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn("Top Seeds Rankings", content)
        self.assertIn("team-ranking-group-top-seeds", content)
        self.assertIn(self.team.display_backend, content)

    def test_rank_debaters_includes_ranking_group_section(self):
        ranking_group = RankingGroup.objects.create(name="Speaker Showcase")
        ranking_group.debaters.add(self.debater)

        response = self.client.get(reverse("rank_debaters"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn("Speaker Showcase Rankings", content)
        self.assertIn("debater-ranking-group-speaker-showcase", content)
        self.assertIn(self.debater.name, content)

    def test_manage_ranking_groups_post_creates_group(self):
        response = self.client.post(
            reverse("manage_ranking_groups"),
            {
                "name": "Create From Manage",
                "teams": [self.team.id],
                "debaters": [self.debater.id],
            },
        )
        self.assertEqual(response.status_code, 302)
        ranking_group = RankingGroup.objects.get(name="Create From Manage")
        self.assertEqual(list(ranking_group.teams.all()), [self.team])
        self.assertEqual(list(ranking_group.debaters.all()), [self.debater])

    def test_ranking_group_delete_existing_group(self):
        ranking_group = RankingGroup.objects.create(name="Delete Me")
        response = self.client.post(
            reverse("ranking_group", args=[ranking_group.id]),
            {"_method": "DELETE"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(RankingGroup.objects.filter(id=ranking_group.id).exists())

    def test_view_teams_shows_ranking_group_badges(self):
        ranking_group = RankingGroup.objects.create(name="Team Badge Group")
        ranking_group.teams.add(self.team)
        response = self.client.get(reverse("view_teams"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Team Badge Group", content)
        self.assertIn("ranking-badges", content)
