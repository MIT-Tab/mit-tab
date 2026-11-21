from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from mittab.apps.tab.forum_publication import dino_roster
from mittab.apps.tab.models import Judge, Outround, TabSettings, Team


@override_settings(
    APDA_FORUM_URL="https://forum.example.test",
    APDA_FORUM_PUBLICATION_ID="a" * 32,
    APDA_FORUM_TOURNAMENT_TOKEN="test-token",
)
class ForumPostTest(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
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
        self.client.post(self.url, {"confirm_dinos": dino_roster()[1]})
        publish.assert_not_called()

    @patch("mittab.apps.tab.views.views._forum_results")
    @patch("mittab.apps.tab.forum_publication.publish_results")
    def test_zero_dinos_can_be_confirmed(self, publish, context):
        self.pair(Outround.VARSITY, decision=Outround.GOV)
        context.return_value = {
            "team_count": 0, "novice_count": 0, "dinos": [],
            "varsity_champ": self.gov, "novice_champ": None,
            "varsity_debaters": [], "nov_debaters": [],
            "varsity_teams": [], "novice_teams": [],
            "varsity_outs": [], "novice_outs": [],
        }
        self.assertContains(self.client.get(self.url), "No judges are marked as dinos.")
        publish.return_value = "https://forum.example.test/forum/topic/results/#post-1"
        response = self.client.post(self.url, {"confirm_dinos": dino_roster()[1]})
        self.assertRedirects(
            response, self.url, fetch_redirect_response=False,
        )
        self.assertIn("Varsity champions", publish.call_args.args[0])
        self.assertEqual(TabSettings.get("forum_results_url"), publish.return_value)

    @patch("mittab.apps.tab.forum_publication.publish_results")
    def test_changed_dinos_require_new_confirmation(self, publish):
        self.pair(Outround.VARSITY, decision=Outround.OPP_VIA_FORFEIT)
        self.pair(Outround.NOVICE, decision=Outround.GOV)
        old_confirmation = dino_roster()[1]
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
