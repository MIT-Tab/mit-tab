from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.registration.models import InfoLink, InfoText
from mittab.apps.tab.auth_roles import (
    PRESET_DATA_ENTRY_HELPER,
    apply_staff_permission_preset,
)
from mittab.apps.tab.models import TabSettings
from mittab.apps.tab.tournament_start import START_TOURNAMENT_WIZARD_COMPLETED
from mittab.apps.tab.views.tournament_start_views import WizardStaffInviteForm
from mittab.libs.email_service import EmailServiceError

SEND_INVITE = (
    "mittab.apps.tab.views.tournament_start_views.send_staff_invite_email"
)


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
            "open_speaks": 1,
            "open_ranks": 2,
            "invite-email": "second@test.com",
            "invite-username": "second_tab",
            "invite-permission_preset": "full_tab_staff",
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
        self.assertContains(response, "Invite at least one more member of tab staff")
        self.assertContains(response, "Do not share your password")
        self.assertNotContains(response, "Preliminary rounds")

    def test_initialization_placeholders_are_not_prefilled(self):
        InfoText.ensure_defaults()
        InfoLink.objects.create(
            title="Facebook",
            url="https://www.facebook.com/events/previous-tournament",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("start_tournament"))

        form = response.context["form"]
        self.assertEqual(form.initial["tournament_name"], "")
        self.assertEqual(form.initial["ga_room"], "")
        self.assertEqual(form.initial["facebook_url"], "")

    def test_invite_form_can_be_validated_without_an_inviter(self):
        form = WizardStaffInviteForm({
            "email": "second@test.com",
            "username": "second_tab",
            "permission_preset": "full_tab_staff",
        })

        self.assertTrue(form.is_valid())

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
        InfoText.objects.all().delete()

        with mock.patch(SEND_INVITE) as send_invite:
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
        self.assertEqual(TabSettings.get("tot_rounds"), 5)
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

        invitee = get_user_model().objects.get(username="second_tab")
        self.assertEqual(invitee.email, "second@test.com")
        self.assertTrue(invitee.is_staff)
        self.assertFalse(invitee.has_usable_password())
        send_invite.assert_called_once()
        self.assertEqual(send_invite.call_args.args[1], invitee)

        unlocked_response = self.client.get(reverse("index"))
        self.assertEqual(unlocked_response.status_code, 200)

    def test_staff_invite_is_required_for_initial_setup(self):
        self.client.force_login(self.user)
        data = self._valid_data()
        del data["invite-email"]
        del data["invite-username"]

        with mock.patch(SEND_INVITE) as send_invite:
            response = self.client.post(reverse("start_tournament"), data)

        self.assertEqual(response.status_code, 200)
        send_invite.assert_not_called()
        self.assertEqual(TabSettings.get(START_TOURNAMENT_WIZARD_COMPLETED), 0)
        self.assertEqual(TabSettings.get("tournament_name"), "New Tournament")

    def test_cannot_invite_yourself(self):
        self.client.force_login(self.user)
        data = self._valid_data()
        data["invite-email"] = "wizard@test.com"
        data["invite-username"] = "wizard_user"

        with mock.patch(SEND_INVITE) as send_invite:
            response = self.client.post(reverse("start_tournament"), data)

        self.assertContains(response, "Invite a different person")
        send_invite.assert_not_called()
        self.assertEqual(TabSettings.get(START_TOURNAMENT_WIZARD_COMPLETED), 0)

    def test_failed_invite_email_does_not_unlock_staff_tools(self):
        self.client.force_login(self.user)

        with mock.patch(SEND_INVITE, side_effect=EmailServiceError("SES down")):
            response = self.client.post(
                reverse("start_tournament"),
                self._valid_data(),
            )

        self.assertContains(response, "Could not send the staff invite: SES down")
        self.assertEqual(TabSettings.get(START_TOURNAMENT_WIZARD_COMPLETED), 0)
        self.assertEqual(TabSettings.get("tournament_name"), "New Tournament")
        self.assertFalse(
            get_user_model().objects.filter(username="second_tab").exists()
        )

    def test_failed_reinvite_preserves_existing_staff_permissions(self):
        existing_user = get_user_model().objects.create_user(
            username="second_tab",
            email="second@test.com",
            is_staff=True,
        )
        apply_staff_permission_preset(existing_user, PRESET_DATA_ENTRY_HELPER)
        self.client.force_login(self.user)

        with mock.patch(SEND_INVITE, side_effect=EmailServiceError("SES down")):
            response = self.client.post(
                reverse("start_tournament"),
                self._valid_data(),
            )

        self.assertContains(response, "Could not send the staff invite: SES down")
        existing_user.refresh_from_db()
        self.assertFalse(existing_user.is_superuser)
        self.assertTrue(
            existing_user.groups.filter(
                name="MIT-TAB: Data entry helper",
            ).exists()
        )

    def test_completed_wizard_can_be_edited_from_setup(self):
        self.client.force_login(self.user)
        TabSettings.set(START_TOURNAMENT_WIZARD_COMPLETED, 1)
        InfoText.objects.create(title="GA", text="Old room")
        InfoLink.objects.create(
            title="facebook",
            url="https://www.facebook.com/events/old",
        )

        data = self._valid_data()
        for key in ("invite-email", "invite-username", "invite-permission_preset"):
            del data[key]

        with mock.patch(SEND_INVITE) as send_invite:
            response = self.client.post(reverse("start_tournament"), data)

        send_invite.assert_not_called()
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
