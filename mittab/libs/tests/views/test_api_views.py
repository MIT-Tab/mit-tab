from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
from nplusone.core import profiler

from mittab.apps.tab.models import (
    BreakingTeam, Room, TabSettings, Team, Outround, Round
)
from mittab.apps.tab.public_rankings import set_standings_publication_setting
from mittab.apps.tab.views import api_views as api_views_module


@pytest.mark.django_db(transaction=True)
class TestApiViews(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        caches["default"].clear()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username='testuser',
            password='testpass123',
            email='test@test.com'
        )
        self.client.login(username='testuser', password='testpass123')

        TabSettings.set("cur_round", 2)
        TabSettings.set("results_published", 1)
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
            payload = data[json_key]
            self.assertIsInstance(payload, expected_type,
                f"Expected {json_key} to be {expected_type.__name__} in {url}")

            if not item_key:
                continue

            if isinstance(payload, list):
                if payload and isinstance(payload[0], dict):
                    self.assertIn(item_key, payload[0],
                        f"Expected key '{item_key}' in first item of {json_key}")
            elif isinstance(payload, dict):
                self.assertIn(item_key, payload,
                    f"Expected key '{item_key}' in {json_key}")

        stats_views = [
            (
                reverse("team_stats", args=[round_obj.round_number]),
                dict,
                ("seed", "debaters"),
            ),
        ]

        if outround:
            stats_views.append(
                (
                    reverse("outround_team_stats", args=[outround.pk]),
                    dict,
                    ("seed", "debaters"),
                )
            )

        for url, expected_type, item_keys in stats_views:
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
                for item_key in item_keys:
                    self.assertIn(item_key, data[first_team_id],
                        f"Expected key '{item_key}' in team stats")

    def test_team_stats_debaters_ignore_public_setting(self):
        round_obj = Round.objects.filter(round_number=1).first()
        TabSettings.set("debaters_public", 0)

        response = self.client.get(reverse("team_stats", args=[round_obj.round_number]))
        self.assertEqual(response.status_code, 200)
        data = response.json()

        for team in (round_obj.gov_team, round_obj.opp_team):
            expected_debaters = ", ".join(
                debater.name for debater in team.debaters.all()
            )
            self.assertIn(str(team.id), data)
            self.assertEqual(data[str(team.id)]["debaters"], expected_debaters)

    def test_debater_counts_supports_control_board(self):
        response = self.client.get(reverse("debater_counts_api"))
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertIn("debater_counts", data)
        counts = data["debater_counts"]
        for key in ("varsity", "novice", "teams"):
            self.assertIn(key, counts,
                f"Expected '{key}' in debater counts for control board")
            self.assertIsInstance(counts[key], int,
                f"Expected '{key}' count to be int")

    def test_publication_settings_gate_api_access(self):
        speaker_urls = [
            reverse("varsity_speaker_awards_api"),
            reverse("novice_speaker_awards_api"),
        ]
        team_urls = [
            reverse("varsity_team_placements_api"),
            reverse("novice_team_placements_api"),
            reverse("non_placing_teams_api"),
        ]
        shared_urls = [
            reverse("new_debater_data_api"),
            reverse("new_schools_api"),
            reverse("debater_counts_api"),
        ]

        # Disable both publications while the three-hour embargo is active.
        set_standings_publication_setting("speaker_results", False)
        set_standings_publication_setting("team_results", False)
        for url in speaker_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 423)
        # Re-enable to unblock future checks
        set_standings_publication_setting("speaker_results", True)

        # Outround/team placements are live as soon as entered.
        for url in team_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

        # Identity/count APIs remain available so early live teams can resolve.
        set_standings_publication_setting("speaker_results", False)
        for url in shared_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

        # Restore defaults for downstream tests
        set_standings_publication_setting("speaker_results", True)
        set_standings_publication_setting("team_results", True)

    def test_speaker_api_unlocks_three_hours_after_final_inround(self):
        set_standings_publication_setting("speaker_results", False)
        set_standings_publication_setting("team_results", False)
        final_round = Round.objects.order_by("-round_number").first().round_number
        TabSettings.set("tot_rounds", final_round)
        completed_at = timezone.now() - timedelta(hours=3, minutes=1)
        Round.objects.filter(round_number=final_round).update(
            victor=Round.GOV,
            result_entered_at=completed_at,
        )

        response = self.client.get(reverse("varsity_speaker_awards_api"))

        self.assertEqual(response.status_code, 200)

    def test_live_results_redacts_speakers_during_embargo(self):
        set_standings_publication_setting("speaker_results", False)
        set_standings_publication_setting("team_results", False)

        response = self.client.get(reverse("live_results_api"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["availability"]["speakers"])
        self.assertEqual(response.json()["speaker_results"], [])

    def test_live_results_is_public(self):
        self.client.logout()

        response = self.client.get(reverse("live_results_api"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["schema_version"], 1)

    def test_live_results_reports_guaranteed_partial_bracket_places(self):
        TabSettings.set("var_teams_visible", 8)
        outround = Outround.objects.filter(type_of_round=Outround.VARSITY).first()
        outround.num_teams = 8
        outround.victor = Outround.GOV
        outround.save()

        response = self.client.get(reverse("live_results_api"))

        self.assertEqual(response.status_code, 200)
        varsity_results = [
            result for result in response.json()["team_results"]
            if result["division"] == "varsity"
        ]
        self.assertEqual(
            {result["place"] for result in varsity_results},
            {3, 5},
        )

    def _prepare_live_release(self):
        self.client.logout()
        Outround.objects.all().delete()
        BreakingTeam.objects.all().delete()
        for division in ("var", "nov"):
            TabSettings.set(f"{division}_teams_visible", 256)
        for slug in ("team_results", "speaker_results"):
            set_standings_publication_setting(slug, False)
        final_round = Round.objects.order_by("-round_number").first().round_number
        TabSettings.set("tot_rounds", final_round)
        Round.objects.filter(round_number=final_round).update(
            victor=Round.GOV, result_entered_at=timezone.now(),
        )
        teams = list(Team.objects.order_by("pk")[:4])
        outround = Outround.objects.create(
            gov_team=teams[0], opp_team=teams[1], num_teams=8,
            type_of_round=Outround.VARSITY, room=Room.objects.first(),
        )
        return teams, outround

    def _live_payload(self):
        caches["default"].clear()
        response = self.client.get(reverse("live_results_api"))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_unreleased_pairings_and_results_do_not_unlock_live_results(self):
        _teams, outround = self._prepare_live_release()
        outround.victor = Outround.GOV
        outround.save()
        payload = self._live_payload()
        self.assertFalse(payload["availability"]["speakers"])
        self.assertEqual(payload["speaker_results"], [])
        self.assertEqual(payload["team_results"], [])
        self.assertEqual(
            self.client.get(reverse("varsity_speaker_awards_api")).status_code, 423,
        )
        # Publishing final team standings is not the pairing release control.
        set_standings_publication_setting("team_results", True)
        self.assertFalse(self._live_payload()["availability"]["speakers"])

    def test_first_released_outround_unlocks_top_ten_speakers_without_results(self):
        self._prepare_live_release()
        for division, key in ((Outround.VARSITY, "var"), (Outround.NOVICE, "nov")):
            with self.subTest(division=division):
                Outround.objects.update(type_of_round=division)
                TabSettings.set("var_teams_visible", 256)
                TabSettings.set("nov_teams_visible", 256)
                TabSettings.set(f"{key}_teams_visible", 8)
                payload = self._live_payload()
                self.assertTrue(payload["availability"]["speakers"])
                self.assertTrue(payload["speaker_results"])
                for row in payload["speaker_results"]:
                    self.assertEqual(set(row), {"place", "division", "tie", "debater"})
                    self.assertEqual(set(row["debater"]), {
                        "apda_id", "tournament_id", "name", "novice", "school",
                    })
                for name in ("varsity", "novice"):
                    self.assertLessEqual(sum(
                        row["division"] == name for row in payload["speaker_results"]
                    ), 10)
                    self.assertEqual(self.client.get(reverse(
                        f"{name}_speaker_awards_api"
                    )).status_code, 200)

    def test_quarterfinal_points_increase_as_team_advances(self):
        teams, outround = self._prepare_live_release()
        TabSettings.set("var_teams_visible", 8)

        def places():
            rows = self._live_payload()["team_results"]
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(set(row), {"place", "division", "team"})
                self.assertEqual(set(row["team"]), {
                    "tournament_id", "name", "hybrid", "debaters",
                })
            return {row["team"]["tournament_id"]: row["place"] for row in rows}

        self.assertEqual(places(), {teams[0].pk: 5, teams[1].pk: 5})
        outround.victor = Outround.GOV
        outround.save()
        self.assertEqual(places(), {teams[0].pk: 3, teams[1].pk: 5})
        semifinal = Outround.objects.create(
            gov_team=teams[0], opp_team=teams[2], num_teams=4,
            type_of_round=Outround.VARSITY, room=Room.objects.first(),
        )
        # Pairing a private later round does not add its other participant.
        self.assertEqual(places(), {teams[0].pk: 3, teams[1].pk: 5})
        semifinal.victor = Outround.GOV
        semifinal.save()
        self.assertEqual(places(), {teams[0].pk: 3, teams[1].pk: 5})
        TabSettings.set("var_teams_visible", 4)
        rows = self._live_payload()["team_results"]
        self.assertEqual({row["team"]["tournament_id"]: row["place"] for row in rows}, {
            teams[0].pk: 2, teams[1].pk: 5, teams[2].pk: 3,
        })
        final = Outround.objects.create(
            gov_team=teams[0], opp_team=teams[3], num_teams=2,
            type_of_round=Outround.VARSITY, room=Room.objects.first(),
        )
        TabSettings.set("var_teams_visible", 2)
        final.victor = Outround.GOV
        final.save()
        rows = self._live_payload()["team_results"]
        self.assertEqual({row["team"]["tournament_id"]: row["place"] for row in rows}, {
            teams[0].pk: 1, teams[1].pk: 5, teams[2].pk: 3, teams[3].pk: 2,
        })

    def test_partial_opening_round_byes_receive_next_round_points(self):
        teams, outround = self._prepare_live_release()
        outround.num_teams = 16
        outround.save()
        for division, key in ((Outround.VARSITY, "var"), (Outround.NOVICE, "nov")):
            with self.subTest(division=division):
                Outround.objects.update(type_of_round=division)
                BreakingTeam.objects.all().delete()
                for team in teams[:3]:
                    BreakingTeam.objects.create(team=team, type_of_team=division)
                TabSettings.set(f"{key}_teams_visible", 16)
                rows = self._live_payload()["team_results"]
                self.assertTrue(all(row["division"] == (
                    "varsity" if division == Outround.VARSITY else "novice"
                ) for row in rows))
                self.assertEqual({
                    row["team"]["tournament_id"]: row["place"] for row in rows
                }, {teams[0].pk: 9, teams[1].pk: 9, teams[2].pk: 5})

    def test_speaker_fallback_without_outrounds_respects_three_hour_boundary(self):
        self._prepare_live_release()
        Outround.objects.all().delete()
        final_round = int(TabSettings.get("tot_rounds"))
        now = timezone.now()
        for elapsed, available in ((timedelta(hours=2, minutes=59), False),
                                   (timedelta(hours=3), True)):
            Round.objects.filter(round_number=final_round).update(
                result_entered_at=now - elapsed,
            )
            with patch("mittab.apps.tab.live_results.timezone.now", return_value=now):
                payload = self._live_payload()
            self.assertEqual(payload["availability"]["speakers"], available)
            self.assertEqual(bool(payload["speaker_results"]), available)
            self.assertEqual(payload["team_results"], [])

    def test_live_results_is_cached_between_requests(self):
        self.client.get(reverse("live_results_api"))

        with patch.object(api_views_module, "_live_team_results") as rebuild:
            response = self.client.get(reverse("live_results_api"))

        rebuild.assert_not_called()
        self.assertEqual(response.status_code, 200)

    def test_live_results_dates_tournament_by_first_result(self):
        TabSettings.set("tournament_date", "2026-08-01")
        held_on = timezone.now() - timedelta(days=30)
        Round.objects.update(result_entered_at=held_on)

        tournament = self.client.get(reverse("live_results_api")).json()["tournament"]

        expected = timezone.localdate(held_on)
        self.assertEqual(tournament["date"], expected.isoformat())
        self.assertEqual(
            tournament["season"],
            str(expected.year if expected.month >= 7 else expected.year - 1),
        )

    def test_round_records_when_a_result_is_first_entered(self):
        round_obj = Round.objects.first()
        round_obj.victor = Round.UNKNOWN
        round_obj.save()
        self.assertIsNone(round_obj.result_entered_at)

        round_obj.victor = Round.GOV
        round_obj.save()

        self.assertIsNotNone(round_obj.result_entered_at)

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
            ("live_results_api",),
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
