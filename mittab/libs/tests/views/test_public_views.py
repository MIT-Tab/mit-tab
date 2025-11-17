import pytest
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase, Client
from django.urls import reverse
from nplusone.core import profiler

from mittab.apps.tab.models import (Room, TabSettings, Team,
                                    Round, Outround)
from mittab.apps.tab.public_rankings import (
    get_ballot_round_settings,
    get_ranking_settings,
    get_standings_publication_setting,
    set_ballot_round_settings,
    set_ranking_settings,
)
from mittab.libs.cacheing import cache_logic
from mittab.libs.tests.views.public_test_utils import (
    prepare_public_site_state,
    reset_public_site_state,
)


@pytest.mark.django_db(transaction=True)
class TestPublicViews(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.test_round, self.original_victor = prepare_public_site_state()

    def tearDown(self):
        # Restore the original victor value to avoid polluting other tests
        reset_public_site_state(self.test_round, self.original_victor)
        super().tearDown()

    def get_test_objects(self):
        judge = self.test_round.chair
        team = Team.objects.first()
        round_obj = Round.objects.filter(round_number=1, gov_team__isnull=False).first()
        v_out = Outround.objects.filter(type_of_round=0).first()
        n_out = Outround.objects.filter(type_of_round=1).first()
        gov_debater = round_obj.gov_team.debaters.first()

        # Ensure judge has a ballot code for e-ballot tests
        judge.ballot_code = "TEST123"
        judge.save()

        return judge, team, round_obj, v_out, n_out, gov_debater

    def test_render(self):
        client = Client()

        (judge, team, round_obj, v_out,
         n_out, gov_debater) = self.get_test_objects()

        view_content_tests = [
            (reverse("public_judges"), [judge.name]),
            (reverse("public_teams"), [team.name]),
            (reverse("rank_teams_public"), [team.name]),
            (reverse("public_speaker_rankings"), ["Speaker Rankings"]),
            (reverse("public_ballots"), ["Public Ballots"]),
            (reverse("pretty_pair"), [round_obj.gov_team.name, gov_debater.name]),
            (reverse("missing_ballots"), [self.test_round.chair.name]),
            (reverse("e_ballot_search"), ["Submit E-Ballot"]),
            (reverse("outround_pretty_pair", args=[0]), [v_out.gov_team.name]),
            (reverse("outround_pretty_pair", args=[1]), [n_out.gov_team.name]),
            (reverse("public_home"), ["Released Pairings"]),
        ]

        for url, expected_content in view_content_tests:
            response = client.get(url)
            self.assertEqual(response.status_code, 200,
                f"Failed to render {url}, got status {response.status_code}")

            for content in expected_content:
                self.assertIn(content, response.content.decode(),
                    f"Expected content '{content}' not found in {url}")

        if judge and judge.ballot_code:
            chair_round = Round.objects.filter(round_number=1).first()
            if chair_round:
                chair_round.chair = judge
                chair_round.save()

                url = reverse("enter_e_ballot", args=[judge.ballot_code])
                response = client.get(url)
                self.assertIn(response.status_code, [200, 302],
                    "Failed to handle e-ballot entry for valid ballot code")

    def test_permissions(self):
        client = Client()

        (judge, team, _, v_out,
         n_out, gov_debater) = self.get_test_objects()

        # Judges list
        response = client.get(reverse("public_judges"))
        self.assertIn(judge.name, response.content.decode())
        TabSettings.set("judges_public", 0)
        caches["public"].clear()
        response = client.get(reverse("public_judges"))
        self.assertEqual(response.status_code, 302)
        TabSettings.set("judges_public", 1)
        caches["public"].clear()

        # Teams list
        response = client.get(reverse("public_teams"))
        self.assertIn(team.name, response.content.decode())
        TabSettings.set("teams_public", 0)
        caches["public"].clear()
        response = client.get(reverse("public_teams"))
        self.assertEqual(response.status_code, 302)
        TabSettings.set("teams_public", 1)
        caches["public"].clear()

        # Team results page
        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        set_ranking_settings("team", public=False, include_speaks=False, max_visible=1000)
        caches["public"].clear()
        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 302)
        set_ranking_settings("team", public=True, include_speaks=False, max_visible=1000)
        caches["public"].clear()

        # Speaker results require either varsity or novice to be public
        response = client.get(reverse("public_speaker_rankings"))
        self.assertEqual(response.status_code, 200)
        set_ranking_settings("varsity", public=False, include_speaks=True, max_visible=10)
        set_ranking_settings("novice", public=False, include_speaks=False, max_visible=10)
        caches["public"].clear()
        response = client.get(reverse("public_speaker_rankings"))
        self.assertEqual(response.status_code, 302)
        set_ranking_settings("varsity", public=True, include_speaks=True, max_visible=10)
        set_ranking_settings("novice", public=True, include_speaks=False, max_visible=10)
        caches["public"].clear()

        # Ballots page requires at least one round to be visible
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 200)
        set_ballot_round_settings(1, visible=False, include_speaks=False, include_ranks=False)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 302)
        set_ballot_round_settings(1, visible=True, include_speaks=False, include_ranks=False)
        caches["public"].clear()

        # Pairings require release toggle
        response = client.get(reverse("pretty_pair"))
        self.assertIn(gov_debater.name, response.content.decode())
        TabSettings.set("pairing_released", 0)
        caches["public"].clear()
        response = client.get(reverse("pretty_pair"))
        self.assertEqual(response.status_code, 200)
        TabSettings.set("pairing_released", 1)
        caches["public"].clear()

        # Outround visibility thresholds
        response = client.get(reverse("outround_pretty_pair", args=[0]))
        self.assertIn(v_out.gov_team.name, response.content.decode())
        response = client.get(reverse("outround_pretty_pair", args=[1]))
        self.assertIn(n_out.gov_team.name, response.content.decode())
        TabSettings.set("var_teams_visible", 16)
        TabSettings.set("nov_teams_visible", 16)
        caches["public"].clear()
        response = client.get(reverse("outround_pretty_pair", args=[0]))
        self.assertEqual(response.status_code, 200)
        response = client.get(reverse("outround_pretty_pair", args=[1]))
        self.assertEqual(response.status_code, 200)
        TabSettings.set("var_teams_visible", 2)
        TabSettings.set("nov_teams_visible", 2)
        caches["public"].clear()


    def test_team_rankings_without_speaks(self):
        client = Client()

        set_ranking_settings("team", public=True, include_speaks=False, max_visible=1000)
        caches["public"].clear()

        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Speaks and ranks are hidden", content)
        self.assertNotIn("<th>Speaks</th>", content)

        set_ranking_settings("team", public=True, include_speaks=True, max_visible=1000)
        caches["public"].clear()
        response = client.get(reverse("rank_teams_public"))
        self.assertIn("<th>Speaks</th>", response.content.decode())

    def test_speaker_rankings_visibility(self):
        client = Client()

        response = client.get(reverse("public_speaker_rankings"))
        content = response.content.decode()
        self.assertIn("Varsity Speakers", content)
        self.assertIn("Novice Speakers", content)

        set_ranking_settings("varsity", public=False, include_speaks=True, max_visible=10)
        caches["public"].clear()
        response = client.get(reverse("public_speaker_rankings"))
        content = response.content.decode()
        self.assertNotIn("Varsity Speakers", content)
        self.assertIn("Novice Speakers", content)

    def test_public_ballots_include_scores(self):
        client = Client()

        set_ballot_round_settings(1, visible=True, include_speaks=False, include_ranks=False)
        caches["public"].clear()
        cache_logic.invalidate_cache("public_ballots_round_1")

        response = client.get(reverse("public_ballots"))
        content = response.content.decode()
        self.assertNotIn("badge badge-light", content)

        set_ballot_round_settings(1, visible=True, include_speaks=True, include_ranks=True)
        caches["public"].clear()
        cache_logic.invalidate_cache("public_ballots_round_1")
        response = client.get(reverse("public_ballots"))
        content = response.content.decode()
        self.assertIn("badge badge-light", content)

    def test_n_plus_one(self):
        client = Client()

        views_to_test = [
            (("public_judges",), None),
            (("public_teams",), None),
            (("rank_teams_public",), None),
             (("public_speaker_rankings",), None),
             (("public_ballots",), None),
            (("pretty_pair",), None),
            (("missing_ballots",), None),
            (("outround_pretty_pair",), [0]),
            (("outround_pretty_pair",), [1]),
            (("public_home",), None),
        ]

        for view_name, url_args in views_to_test:
            with profiler.Profiler():
                if url_args:
                    response = client.get(reverse(*view_name, args=url_args))
                else:
                    response = client.get(reverse(*view_name))
                self.assertEqual(response.status_code, 200)


@pytest.mark.django_db(transaction=True)
class TestPublicRankingsControl(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="control_user",
            password="controlpass",
            email="control@example.com",
        )
        self.client.login(username="control_user", password="controlpass")
        self.test_round, self.original_victor = prepare_public_site_state()

    def tearDown(self):
        reset_public_site_state(self.test_round, self.original_victor)
        super().tearDown()

    def test_get_control_panel(self):
        response = self.client.get(reverse("public_rankings_control"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Public Rankings Control Panel", response.content.decode())

    def test_update_public_display_settings(self):
        payload = {
            "standings_team_results": "on",
            "team_public": "on",
            "team_include_speaks": "on",
            "team_max_visible": "25",
            # Varsity intentionally left unpublished to verify false path
            "novice_public": "on",
            "novice_include_speaks": "",
            "novice_max_visible": "5",
            "round_1_visible": "on",
            "round_1_include_speaks": "on",
            # Leave include_ranks off
        }

        response = self.client.post(
            reverse("public_rankings_control"),
            payload,
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        speaker_setting = get_standings_publication_setting("speaker_results")
        team_setting = get_standings_publication_setting("team_results")
        self.assertFalse(speaker_setting["published"])
        self.assertTrue(team_setting["published"])

        team_rankings = get_ranking_settings("team")
        self.assertTrue(team_rankings["public"])
        self.assertTrue(team_rankings["include_speaks"])
        self.assertEqual(team_rankings["max_visible"], 25)

        varsity_rankings = get_ranking_settings("varsity")
        novice_rankings = get_ranking_settings("novice")
        self.assertFalse(varsity_rankings["public"])
        self.assertTrue(novice_rankings["public"])
        self.assertFalse(novice_rankings["include_speaks"])
        self.assertEqual(novice_rankings["max_visible"], 5)

        ballot_settings = get_ballot_round_settings(1)
        self.assertTrue(ballot_settings["visible"])
        self.assertTrue(ballot_settings["include_speaks"])
        self.assertFalse(ballot_settings["include_ranks"])
