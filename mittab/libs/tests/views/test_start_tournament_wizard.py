import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.registration.models import InfoLink, InfoText
from mittab.apps.tab.models import TabSettings
from mittab.apps.tab.tournament_start import START_TOURNAMENT_WIZARD_COMPLETED


@pytest.mark.django_db(transaction=True)
class TestStartTournamentWizard(TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="wizard_user",
            password="wizard_pass_123",
            email="wizard@test.com",
        )
        TabSettings.set("tournament_name", "New Tournament")
        TabSettings.set("tot_rounds", 5)
        TabSettings.set(START_TOURNAMENT_WIZARD_COMPLETED, 0)

    def _valid_data(self):
        return {
            "tournament_name": "MIT Fall Invitational",
            "ga_room": "Building 26, Room 100",
            "facebook_url": "https://www.facebook.com/events/123456789",
            "total_rounds": 6,
            "varsity_break_size": 16,
            "novice_break_size": 8,
            "open_speaks": 1,
            "open_ranks": 2,
        }

    def test_first_admin_login_redirects_to_wizard_even_with_next(self):
        response = self.client.post(
            f'{reverse("tab_login")}?next={reverse("index")}',
            {"username": "wizard_user", "password": "wizard_pass_123"},
        )

        self.assertRedirects(
            response,
            reverse("start_tournament"),
            fetch_redirect_response=False,
        )

    def test_incomplete_wizard_blocks_staff_pages(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("index"))

        self.assertRedirects(
            response,
            reverse("start_tournament"),
            fetch_redirect_response=False,
        )

    def test_wizard_page_explains_gate_and_future_edits(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("start_tournament"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete these basics to unlock staff tools")
        self.assertContains(response, "You can change every answer later")
        self.assertContains(response, "Preliminary rounds")

    def test_initialization_placeholders_are_not_prefilled(self):
        InfoText.ensure_defaults()
        self.client.force_login(self.user)

        response = self.client.get(reverse("start_tournament"))

        form = response.context["form"]
        self.assertEqual(form.initial["tournament_name"], "")
        self.assertEqual(form.initial["ga_room"], "")

    def test_invalid_form_does_not_unlock_staff_tools(self):
        self.client.force_login(self.user)
        data = self._valid_data()
        data["facebook_url"] = "not a url"

        response = self.client.post(reverse("start_tournament"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid URL")
        self.assertEqual(TabSettings.get(START_TOURNAMENT_WIZARD_COMPLETED), 0)

    def test_valid_form_saves_all_basics_and_unlocks_staff_tools(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("start_tournament"),
            self._valid_data(),
        )

        self.assertRedirects(
            response,
            reverse("tournament_todo"),
            fetch_redirect_response=False,
        )
        self.assertEqual(TabSettings.get("tournament_name"), "MIT Fall Invitational")
        self.assertEqual(TabSettings.get("tot_rounds"), 6)
        self.assertEqual(TabSettings.get("var_teams_to_break"), 16)
        self.assertEqual(TabSettings.get("nov_teams_to_break"), 8)
        self.assertEqual(TabSettings.get("open_speaks"), 1)
        self.assertEqual(TabSettings.get("open_ranks"), 2)
        self.assertEqual(TabSettings.get(START_TOURNAMENT_WIZARD_COMPLETED), 1)

        ga_info = InfoText.objects.get(title=InfoText.DEFAULT_TITLE)
        self.assertEqual(ga_info.text, "Building 26, Room 100")
        self.assertTrue(ga_info.is_active)

        facebook_link = InfoLink.objects.get(title="Facebook")
        self.assertEqual(
            facebook_link.url,
            "https://www.facebook.com/events/123456789",
        )
        self.assertTrue(facebook_link.is_active)

        unlocked_response = self.client.get(reverse("index"))
        self.assertEqual(unlocked_response.status_code, 200)

    def test_completed_wizard_can_be_edited_from_setup(self):
        self.client.force_login(self.user)
        TabSettings.set(START_TOURNAMENT_WIZARD_COMPLETED, 1)
        InfoText.objects.create(title="GA", text="Old room")
        InfoLink.objects.create(
            title="facebook",
            url="https://www.facebook.com/events/old",
        )

        response = self.client.post(
            reverse("start_tournament"),
            self._valid_data(),
        )

        self.assertRedirects(
            response,
            reverse("start_tournament"),
            fetch_redirect_response=False,
        )
        self.assertEqual(InfoLink.objects.count(), 1)
        self.assertEqual(
            InfoLink.objects.get().url,
            "https://www.facebook.com/events/123456789",
        )

    def test_legacy_tournament_without_completion_setting_is_not_gated(self):
        TabSettings.objects.filter(
            key=START_TOURNAMENT_WIZARD_COMPLETED,
        ).delete()
        self.client.force_login(self.user)

        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
