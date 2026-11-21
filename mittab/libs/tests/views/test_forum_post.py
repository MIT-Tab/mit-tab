from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from mittab.apps.tab import forum_publication
from mittab.apps.tab.forum_publication import ForumPublicationError
from mittab.apps.tab.models import Judge, Outround, TabSettings, Team
from mittab.libs.cacheing import cache_logic


FORUM_SETTINGS = {
    "APDA_FORUM_URL": "https://forum.example.test",
    "APDA_FORUM_PUBLICATION_ID": "a" * 32,
    "APDA_FORUM_TOURNAMENT_TOKEN": "test-token",
}
RESULTS_URL = "https://forum.example.test/forum/topic/results/#post-1"


@override_settings(**FORUM_SETTINGS)
class ForumPostTest(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        cache_key = "tab_settings_forum_results_url"
        cache_logic.invalidate_cache(cache_key, cache_logic.PERSISTENT)
        self.addCleanup(
            cache_logic.invalidate_cache, cache_key, cache_logic.PERSISTENT,
        )
        self.staff = get_user_model().objects.create_superuser(username="forum-tab")
        self.client.force_login(self.staff)
        self.url = reverse("forum_post")
        self.gov = Team.objects.first()
        self.opp = Team.objects.last()
        Outround.objects.all().delete()
        Judge.objects.update(is_dino=False)

    def pair(self, division, size=2, decision=Outround.UNKNOWN):
        return Outround.objects.create(
            gov_team=self.gov, opp_team=self.opp, num_teams=size,
            type_of_round=division, victor=decision,
        )

    @patch("mittab.apps.tab.forum_publication.publish_results")
    def test_missing_finals_block_even_if_directly_submitted(self, publish):
        self.pair(Outround.VARSITY)
        self.pair(Outround.NOVICE, size=4)
        response = self.client.get(self.url)
        self.assertContains(response, "Enter the varsity finals decision.")
        self.assertContains(response, "Enter the novice finals decision.")
        self.assertNotContains(response, "Confirm and post")
        confirmation = {"confirm_dinos": forum_publication.dino_roster()[1]}
        self.client.post(self.url, confirmation)
        publish.assert_not_called()

    @patch("mittab.apps.tab.views.views._forum_results")
    @patch("mittab.apps.tab.forum_publication.requests.post")
    def test_retry_after_confirming_zero_dino_judges(self, post, context):
        self.pair(Outround.VARSITY, decision=Outround.GOV)
        context.return_value = {
            "team_count": 0, "novice_count": 0, "dinos": [],
            "varsity_champ": self.gov, "novice_champ": None,
            "varsity_debaters": [], "nov_debaters": [],
            "varsity_teams": [], "novice_teams": [],
            "varsity_outs": [], "novice_outs": [],
        }
        self.assertContains(self.client.get(self.url), "No judges are marked as dinos.")
        confirmation = {"confirm_dinos": forum_publication.dino_roster()[1]}
        delivered = Mock(status_code=200)
        delivered.json.return_value = {"url": RESULTS_URL}
        post.side_effect = [requests.Timeout(), delivered]
        failed = self.client.post(self.url, confirmation, follow=True)
        self.assertContains(
            failed, "The forum could not confirm publication. Try again.",
        )
        self.assertNotContains(failed, "View forum post")
        self.assertEqual(TabSettings.get("forum_results_url", "", use_cache=False), "")

        response = self.client.post(self.url, confirmation, follow=True)
        self.assertContains(response, "Results posted to the APDA Forum.")
        self.assertContains(response, RESULTS_URL)
        self.assertRedirects(
            response, self.url, fetch_redirect_response=False,
        )
        args, kwargs = post.call_args
        self.assertEqual(post.call_args_list[0], post.call_args_list[1])
        self.assertEqual(args, (
            f"{settings.APDA_FORUM_URL}/wp-json/apda-forum/v1/tournaments/"
            f"{settings.APDA_FORUM_PUBLICATION_ID}/results",
        ))
        self.assertIn(f"Varsity champions: {self.gov.display}", kwargs["json"]["body"])
        self.assertEqual(kwargs["headers"], {"X-APDA-Tournament-Token": "test-token"})
        self.assertEqual(kwargs["timeout"], (3, 10))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(
            TabSettings.get("forum_results_url", use_cache=False), RESULTS_URL,
        )

    @patch("mittab.apps.tab.forum_publication.publish_results")
    def test_changed_dinos_require_new_confirmation(self, publish):
        self.pair(Outround.VARSITY, decision=Outround.OPP_VIA_FORFEIT)
        self.pair(Outround.NOVICE, decision=Outround.GOV)
        old_confirmation = forum_publication.dino_roster()[1]
        judge = Judge.objects.first()
        judge.is_dino = True
        judge.save()
        self.client.post(self.url, {"confirm_dinos": old_confirmation})
        publish.assert_not_called()
        with patch(
            "mittab.apps.tab.views.views._forum_results",
            return_value={"dinos": [judge.name]},
        ):
            response = self.client.get(self.url)
        self.assertContains(response, "Are these all the dino judges")
        self.assertContains(response, judge.name)

    @patch("mittab.apps.tab.forum_publication.publish_results")
    def test_non_apda_sites_have_no_posting_action(self, publish):
        with self.settings(APDA_FORUM_TOURNAMENT_TOKEN=""):
            self.assertNotContains(self.client.get(self.url), "Post to APDA Forum")
            self.assertEqual(self.client.post(self.url).status_code, 403)
        publish.assert_not_called()

    def test_import_api_exposes_posting_id_without_the_token(self):
        client = Client()
        import_url = reverse("import_results_api")
        with self.settings(APDA_FORUM_TOURNAMENT_TOKEN=""):
            inactive = client.get(import_url).json()
        self.assertNotIn("forum_publication_id", inactive)

        response = client.get(import_url)
        self.assertContains(response, settings.APDA_FORUM_PUBLICATION_ID)
        self.assertNotContains(response, settings.APDA_FORUM_TOURNAMENT_TOKEN)
        self.assertEqual(
            response.json()["forum_publication_id"], settings.APDA_FORUM_PUBLICATION_ID,
        )
        live = client.get(reverse("live_results_api")).json()
        self.assertNotIn("forum_publication_id", live)


@override_settings(**FORUM_SETTINGS)
class ForumPublicationTest(SimpleTestCase):
    def test_results_body_includes_awards_placements_and_ties_without_scores(self):
        varsity = SimpleNamespace(display="Varsity Team")
        novice = SimpleNamespace(display="Novice Team")
        context = {
            "team_count": 12,
            "novice_count": 6,
            "dinos": ["Dino One", "Dino Two"],
            "varsity_champ": varsity,
            "novice_champ": novice,
            "varsity_debaters": [
                (SimpleNamespace(name=name), 27.25, 4, varsity, tiebreaker)
                for name, tiebreaker in [
                    ("First Speaker", "Tie not broken"),
                    ("Tied Speaker", ""),
                    ("Third Speaker", ""),
                ]
            ],
            "nov_debaters": [
                (SimpleNamespace(name="Novice Speaker"), 26.75, 5, novice, ""),
            ],
            "varsity_teams": [(varsity, 5, 27.25)],
            "novice_teams": [(novice, 4, 26.75)],
            "varsity_outs": [
                {"label": "Varsity final", "results": ["Varsity Team won."]},
            ],
            "novice_outs": [{"label": "Novice final", "results": ["Novice Team won."]}],
        }
        body = forum_publication.results_body(context)
        for section in (
            "12 teams and 6 novices competed",
            "Thanks to our dino judges: Dino One, Dino Two.",
            "Varsity champions: Varsity Team",
            "Novice champions: Novice Team",
            "Varsity speaker awards\n"
            "1. First Speaker\n1. Tied Speaker\n3. Third Speaker",
            "Novice speaker awards\n1. Novice Speaker",
            "Varsity breaking teams\n1. Varsity Team",
            "Novice breaking teams\n1. Novice Team",
            "Varsity final\nVarsity Team won.",
            "Novice final\nNovice Team won.",
        ):
            self.assertIn(section, body)
        self.assertNotIn("27.25", body)
        self.assertNotIn("Tie not broken", body)

    @patch("mittab.apps.tab.forum_publication.requests.post")
    def test_forum_errors_cannot_be_treated_as_successful_posts(self, post):
        for status in (403, 409, 410, 302, 500):
            with self.subTest(status=status):
                post.return_value.status_code = status
                message = (
                    "topic is unavailable" if status in (403, 409, 410) else "Try again"
                )
                with self.assertRaisesRegex(ForumPublicationError, message):
                    forum_publication.publish_results("Results")

    @patch("mittab.apps.tab.forum_publication.requests.post")
    def test_publication_requires_a_valid_forum_url(self, post):
        post.return_value.status_code = 201
        for payload in (
            {"url": "https://other.example.test/results"},
            {"url": "http://forum.example.test/results"},
            {"url": None},
            [],
        ):
            with self.subTest(payload=payload):
                post.return_value.json.return_value = payload
                with self.assertRaisesRegex(ForumPublicationError, "Try again"):
                    forum_publication.publish_results("Results")

        post.return_value.json.side_effect = ValueError("Invalid JSON")
        with self.assertRaisesRegex(ForumPublicationError, "Try again"):
            forum_publication.publish_results("Results")
