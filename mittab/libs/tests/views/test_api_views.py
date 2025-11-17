import pytest
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from nplusone.core import profiler

from mittab.apps.tab.models import (
    Room, TabSettings, Team, Outround, Round
)
from mittab.apps.tab.public_rankings import set_standings_publication_setting


@pytest.mark.django_db(transaction=True)
class TestApiViews(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username='testuser',
            password='testpass123',
            email='test@test.com'
        )
        self.client.login(username='testuser', password='testpass123')

        TabSettings.set("cur_round", 2)
        set_standings_publication_setting("speaker_results", True)
        set_standings_publication_setting("team_results", True)

        Outround(
            gov_team=Team.objects.first(),
            opp_team=Team.objects.last(),
            num_teams=2,
            type_of_round=Outround.NOVICE,
            room=Room.objects.first(),
        ).save()
        Outround(
            gov_team=Team.objects.first(),
            opp_team=Team.objects.last(),
            num_teams=2,
            type_of_round=Outround.VARSITY,
            room=Room.objects.last(),
        ).save()

    def test_render(self):
        round_obj = Round.objects.filter(round_number=1).first()
        outround = Outround.objects.first()

        api_views = [
            (reverse("varsity_speaker_awards_api"),
             "varsity_speaker_awards", list, "apda_id"),
            (reverse("novice_speaker_awards_api"),
             "novice_speaker_awards", list, "apda_id"),
            (reverse("varsity_team_placements_api"),
             "varsity_team_placements", list, None),
            (reverse("novice_team_placements_api"),
             "novice_team_placements", list, None),
            (reverse("non_placing_teams_api"),
             "non_placing_teams", list, None),
            (reverse("new_debater_data_api"),
             "new_debater_data", list, "name"),
            (reverse("new_schools_api"),
             "new_schools", list, None),
            (reverse("debater_counts_api"),
             "debater_counts", dict, "varsity"),
        ]

        for url, json_key, expected_type, item_key in api_views:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200,
                f"Failed to render API {url}, got status {response.status_code}")

            data = response.json()
            self.assertIn(json_key, data,
                f"Expected JSON key '{json_key}' not found in {url}")
            self.assertIsInstance(data[json_key], expected_type,
                f"Expected {json_key} to be {expected_type.__name__} in {url}")

            if item_key:
                target = None
                if isinstance(data[json_key], list) and data[json_key]:
                    target = data[json_key][0]
                elif isinstance(data[json_key], dict):
                    target = data[json_key]
                if isinstance(target, dict):
                    self.assertIn(item_key, target,
                        f"Expected key '{item_key}' in {json_key}")

        stats_views = [
            (reverse("team_stats", args=[round_obj.round_number]), dict, "seed"),
        ]

        if outround:
            stats_views.append(
                (reverse("outround_team_stats", args=[outround.pk]), dict, "seed")
            )

        for url, expected_type, item_key in stats_views:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200,
                f"Failed to render stats API {url}, got status {response.status_code}")

            data = response.json()
            self.assertIsInstance(data, expected_type,
                f"Expected response to be {expected_type.__name__} in {url}")

            if len(data) > 0:
                first_team_id = list(data.keys())[0]
                self.assertIsInstance(data[first_team_id], dict,
                    f"Expected team data to be dict in {url}")
                self.assertIn(item_key, data[first_team_id],
                    f"Expected key '{item_key}' in team stats")

    def test_unpublished_results(self):
        set_standings_publication_setting("speaker_results", False)
        speaker_api_views = [
            reverse("varsity_speaker_awards_api"),
            reverse("novice_speaker_awards_api"),
        ]
        for url in speaker_api_views:
            response = self.client.get(url)
            self.assertEqual(
                response.status_code, 423,
                f"Expected 423 for unpublished speaker results at {url}, "
                f"got {response.status_code}",
            )
            self.assertIn("error", response.content.decode())

        shared_api_views = [
            reverse("new_debater_data_api"),
            reverse("new_schools_api"),
            reverse("debater_counts_api"),
        ]

        team_api_views = [
            reverse("varsity_team_placements_api"),
            reverse("novice_team_placements_api"),
            reverse("non_placing_teams_api"),
        ]

        # Team endpoints remain accessible while speaker standings are unpublished
        for url in team_api_views + shared_api_views:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

        set_standings_publication_setting("speaker_results", True)
        set_standings_publication_setting("team_results", False)
        for url in team_api_views:
            response = self.client.get(url)
            self.assertEqual(
                response.status_code, 423,
                f"Expected 423 for unpublished team results at {url}, "
                f"got {response.status_code}",
            )
            self.assertIn("error", response.content.decode())

        # Shared endpoints remain available because speaker results are published
        for url in shared_api_views:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

        # Disable both standings exports and ensure shared endpoints lock
        set_standings_publication_setting("speaker_results", False)
        set_standings_publication_setting("team_results", False)
        for url in shared_api_views:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 423)

    def test_n_plus_one(self):
        round_obj = Round.objects.filter(round_number=1).first()

        api_views = [
            ("varsity_speaker_awards_api",),
            ("novice_speaker_awards_api",),
            ("varsity_team_placements_api",),
            ("novice_team_placements_api",),
            ("non_placing_teams_api",),
            ("new_debater_data_api",),
            ("new_schools_api",),
            ("debater_counts_api",),
            ("team_stats", [round_obj.round_number]),
        ]

        for view_tuple in api_views:
            view_name = view_tuple[0]
            args = view_tuple[1] if len(view_tuple) > 1 else None

            with profiler.Profiler():
                if args:
                    response = self.client.get(reverse(view_name, args=args))
                else:
                    response = self.client.get(reverse(view_name))
                self.assertEqual(response.status_code, 200)
