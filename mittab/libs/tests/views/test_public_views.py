import pytest
from django.core.cache import caches
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from nplusone.core import profiler

from mittab.apps.tab.models import (Room, TabSettings, Team,
                                    Round, Outround)
from mittab.apps.tab.public_rankings import (
    get_ballot_round_settings,
    get_public_display_flags,
    get_ranking_settings,
    get_standings_publication_setting,
    set_ballot_round_settings,
    set_ranking_settings,
)
from mittab.libs.cacheing import cache_logic


@pytest.mark.django_db(transaction=True)
class TestPublicViews(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        caches["public"].clear()
        cache_logic.clear_cache()

        # Ensure ballots are eligible for public display regardless of fixture defaults
        Team.objects.update(ranking_public=True)

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

        # Pairing for the next round (3) has started, so round 2 ballots may show
        TabSettings.set("cur_round", 3)
        TabSettings.set("tot_rounds", 5)

        # Mark the most recent round (cur_round - 1) as missing a ballot
        self.test_round = Round.objects.filter(
            round_number=TabSettings.get("cur_round") - 1
        ).first()
        self.original_victor = self.test_round.victor
        self.test_round.victor = Round.NONE
        self.test_round.save()
        TabSettings.set("pairing_released", 1)
        TabSettings.set("judges_public", 1)
        TabSettings.set("teams_public", 1)
        TabSettings.set("debaters_public", 1)
        TabSettings.set("var_teams_visible", 2)  # Show finals and above
        TabSettings.set("nov_teams_visible", 2)  # Show finals and above

        set_ranking_settings("team", True, include_speaks=True, max_visible=1000)
        set_ranking_settings("varsity", True, include_speaks=True, max_visible=10)
        set_ranking_settings("novice", True, include_speaks=True, max_visible=10)
        set_ballot_round_settings(1, True, include_speaks=False, include_ranks=False)

    def tearDown(self):
        # Restore the original victor value to avoid polluting other tests
        self.test_round.victor = self.original_victor
        self.test_round.save()
        caches["public"].clear()
        cache_logic.clear_cache()
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

        scenarios = [
            {
                "url": reverse("public_judges"),
                "allowed": lambda: TabSettings.set("judges_public", 1),
                "denied": lambda: TabSettings.set("judges_public", 0),
                "expected": judge.name,
                "status_allowed": 200,
                "status_denied": 302,
            },
            {
                "url": reverse("public_teams"),
                "allowed": lambda: TabSettings.set("teams_public", 1),
                "denied": lambda: TabSettings.set("teams_public", 0),
                "expected": team.name,
                "status_allowed": 200,
                "status_denied": 302,
            },
            {
                "url": reverse("rank_teams_public"),
                "allowed": lambda: set_ranking_settings("team", True, True, 1000),
                "denied": lambda: set_ranking_settings("team", False, True, 1000),
                "expected": team.name,
                "status_allowed": 200,
                "status_denied": 302,
            },
            {
                "url": reverse("pretty_pair"),
                "allowed": lambda: TabSettings.set("pairing_released", 1),
                "denied": lambda: TabSettings.set("pairing_released", 0),
                "expected": gov_debater.name,
                "status_allowed": 200,
                "status_denied": 200,
            },
            {
                "url": reverse("missing_ballots"),
                "allowed": lambda: TabSettings.set("pairing_released", 1),
                "denied": lambda: TabSettings.set("pairing_released", 0),
                "expected": self.test_round.gov_team.display_backend,
                "status_allowed": 200,
                "status_denied": 200,
            },
            {
                "url": reverse("outround_pretty_pair", args=[0]),
                "allowed": lambda: TabSettings.set("var_teams_visible", 2),
                "denied": lambda: TabSettings.set("var_teams_visible", 16),
                "expected": v_out.gov_team.name,
                "status_allowed": 200,
                "status_denied": 200,
            },
            {
                "url": reverse("outround_pretty_pair", args=[1]),
                "allowed": lambda: TabSettings.set("nov_teams_visible", 2),
                "denied": lambda: TabSettings.set("nov_teams_visible", 16),
                "expected": n_out.gov_team.name,
                "status_allowed": 200,
                "status_denied": 200,
            },
        ]

        for scenario in scenarios:
            scenario["allowed"]()
            caches["public"].clear()
            response = client.get(scenario["url"])
            self.assertEqual(
                response.status_code,
                scenario["status_allowed"],
                f"Expected {scenario['status_allowed']} for "
                f" {scenario['url']} when allowed",
            )
            self.assertIn(
                scenario["expected"],
                response.content.decode(),
                f"Expected '{scenario['expected']}' to be "
                f" visible for {scenario['url']}",
            )

            scenario["denied"]()
            caches["public"].clear()
            response = client.get(scenario["url"])
            self.assertEqual(
                response.status_code,
                scenario["status_denied"],
                f"Expected {scenario['status_denied']} for "
                f" {scenario['url']} when denied",
            )
            self.assertNotIn(
                scenario["expected"],
                response.content.decode(),
                f"Expected '{scenario['expected']}' to be hidden for {scenario['url']}",
            )

    def test_public_speaker_rankings_respect_settings(self):
        client = Client()
        response = client.get(reverse("public_speaker_rankings"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Speaker Rankings", content)
        self.assertIn("Varsity Speakers", content)
        self.assertIn("Novice Speakers", content)

        set_ranking_settings("varsity", False, True, 5)
        set_ranking_settings("novice", False, True, 5)
        caches["public"].clear()
        response = client.get(reverse("public_speaker_rankings"))
        self.assertEqual(response.status_code, 302,
            "Speaker rankings should redirect when no divisions are public")

        set_ranking_settings("varsity", True, False, 1)
        caches["public"].clear()
        response = client.get(reverse("public_speaker_rankings"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Varsity Speakers", content)
        self.assertIn("Speaks and ranks are hidden for this section.", content)
        self.assertNotIn("Novice Speakers", content,
            "Hidden divisions should not render a section")

    def test_public_rankings_control_updates_display_settings(self):
        client = Client()
        user = get_user_model().objects.create_superuser(
            username="director",
            email="director@example.com",
            password="password",
        )
        client.force_login(user)
        TabSettings.set("tot_rounds", 2)
        set_ranking_settings("team", False, False, 3)
        set_ranking_settings("varsity", False, True, 3)
        set_ranking_settings("novice", False, True, 3)
        set_ballot_round_settings(1, False, False, False)
        set_ballot_round_settings(2, False, False, False)

        form_data = {
            "standings_team_results": "on",
            "standings_speaker_results": "on",
            "team_public": "on",
            "team_include_speaks": "on",
            "team_max_visible": "25",
            "varsity_max_visible": "5",
            "novice_max_visible": "7",
            "round_1_visible": "on",
            "round_1_include_speaks": "on",
            "round_1_include_ranks": "",
            "round_2_include_speaks": "",
            "round_2_include_ranks": "",
        }
        response = client.post(
            reverse("public_rankings_control"),
            form_data,
        )
        self.assertEqual(response.status_code, 302)

        team_settings = get_ranking_settings("team")
        self.assertTrue(team_settings["public"])
        self.assertTrue(team_settings["include_speaks"])
        self.assertEqual(team_settings["max_visible"], 25)

        speaker_published = get_standings_publication_setting("speaker_results")
        team_published = get_standings_publication_setting("team_results")
        self.assertTrue(speaker_published["published"])
        self.assertTrue(team_published["published"])

        round1_settings = get_ballot_round_settings(1)
        self.assertTrue(round1_settings["visible"])
        self.assertTrue(round1_settings["include_speaks"])
        self.assertFalse(round1_settings["include_ranks"])

        round2_settings = get_ballot_round_settings(2)
        self.assertFalse(round2_settings["visible"],
            "Rounds omitted from the form should remain hidden")

    def test_public_ballot_modes(self):
        client = Client()

        set_ballot_round_settings(1, True, include_speaks=False, include_ranks=False)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Winner:", content)
        self.assertIn("Speaks and ranks are hidden", content)

        set_ballot_round_settings(1, True, include_speaks=True, include_ranks=True)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("Speaks and ranks are hidden", content)

    def test_team_rankings_without_speaks(self):
        client = Client()

        set_ranking_settings("team", True, include_speaks=False, max_visible=1000)
        caches["public"].clear()

        response = client.get(reverse("rank_teams_public"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Speaks and ranks are hidden", content)
        self.assertNotIn("<th>Speaks</th>", content)

    def test_public_ballots_hidden_until_next_round(self):
        client = Client()

        set_ballot_round_settings(1, False, False, False)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 302)

    def test_manual_release_allows_current_round_ballots(self):
        client = Client()

        set_ballot_round_settings(1, False, False, False)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 302)

        set_ballot_round_settings(1, True, True, True)
        caches["public"].clear()
        response = client.get(reverse("public_ballots"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Winner:", response.content.decode())

    def test_public_display_flags_follow_setting_changes(self):
        set_ranking_settings("team", False, True, 1000)
        flags = get_public_display_flags()
        self.assertFalse(flags["team_results"])

        set_ranking_settings("team", True, True, 1000)
        flags = get_public_display_flags()
        self.assertTrue(flags["team_results"])

        set_ballot_round_settings(1, False, False, False)
        flags = get_public_display_flags()
        self.assertFalse(flags["ballots"])

        set_ballot_round_settings(1, True, False, False)
        flags = get_public_display_flags()
        self.assertTrue(flags["ballots"])


    def test_n_plus_one(self):
        client = Client()

        views_to_test = [
            (("public_judges",), None),
            (("public_teams",), None),
            (("rank_teams_public",), None),
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
