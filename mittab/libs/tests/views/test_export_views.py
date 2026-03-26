import json

import pytest
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from nplusone.core import profiler

from mittab.apps.tab.models import (
    Room, TabSettings, Team, Outround
)


@pytest.mark.django_db(transaction=True)
class TestExportViews(TestCase):
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
        team = Team.objects.first()

        csv_exports = [
            reverse("export_pairings_csv"),
            reverse("export_outround_pairings_csv", args=[0]),
            reverse("download_black_rod_bundle"),
        ]

        tab_card_exports = [
            reverse("all_tab_cards"),
            reverse("tab_card", args=[team.pk]),
            reverse("pretty_tab_card", args=[team.pk]),
        ]

        for url in csv_exports:
            response = self.client.get(url)
            self.assertIn(response.status_code, [200, 302],
                f"Failed to render CSV export {url}, got status {response.status_code}")

        for url in tab_card_exports:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200,
                f"Failed to render tab card {url}, got status {response.status_code}")

    def test_black_rod_bundle_export_payload(self):
        response = self.client.get(reverse("download_black_rod_bundle"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json; charset=utf-8")

        payload = json.loads(response.content)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["source"], "mit_tab_black_rod_bundle")
        self.assertIn("schools", payload)
        self.assertIn("debaters", payload)
        self.assertIn("rounds", payload)

        round_row = payload["rounds"][0]
        self.assertIn("import_key", round_row)
        self.assertIn("judges", round_row)
        self.assertIn("victor", round_row)
        self.assertIsInstance(round_row["victor"], int)
        self.assertIn("gov", round_row)
        self.assertIn("opp", round_row)
        self.assertNotIn("team_name", round_row)
        self.assertNotIn("name", round_row["gov"])
        self.assertNotIn("name", round_row["opp"])

        outround_rows = [row for row in payload["rounds"] if row["stage"] == "outround"]
        self.assertTrue(outround_rows)
        self.assertIn(outround_rows[0]["division"], {"varsity", "novice"})
        self.assertIsInstance(outround_rows[0]["elim_size"], int)
        self.assertIn("victor", outround_rows[0])
        self.assertIsInstance(outround_rows[0]["victor"], int)

    def test_n_plus_one(self):
        export_views = [
            ("all_tab_cards",),
        ]

        for view_name_tuple in export_views:
            with profiler.Profiler():
                response = self.client.get(reverse(*view_name_tuple))
                self.assertEqual(response.status_code, 200)
