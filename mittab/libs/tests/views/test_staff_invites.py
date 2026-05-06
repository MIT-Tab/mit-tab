import re
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from mittab.libs.tournament_todo import get_tournament_todo_steps


@pytest.mark.django_db(transaction=True)
class TestStaffInvites(TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="staff_owner",
            password="owner_pass_123",
            email="owner@example.com",
        )
        self.client.login(username="staff_owner", password="owner_pass_123")

    @patch("mittab.apps.tab.staff_invites.EmailService")
    def test_invite_creates_staff_user_and_sends_password_setup_email(
            self, email_service):
        email_service.return_value.send_bulk.return_value = 1

        response = self.client.post(
            reverse("invite_staff"),
            {
                "email": "new.staff@example.com",
                "username": "new_staff",
            },
        )

        self.assertEqual(response.status_code, 302)
        invited_user = get_user_model().objects.get(email="new.staff@example.com")
        self.assertEqual(invited_user.username, "new_staff")
        self.assertTrue(invited_user.is_staff)
        self.assertTrue(invited_user.is_superuser)
        self.assertTrue(invited_user.is_active)
        self.assertFalse(invited_user.has_usable_password())

        email_service.return_value.send_bulk.assert_called_once()
        email_request = email_service.return_value.send_bulk.call_args.args[0][0]
        self.assertEqual(email_request.to_address, invited_user.email)
        self.assertIn("set your password", email_request.text_body)
        self.assertIn("Username: new_staff", email_request.text_body)

        invite_path = self._invite_path_from_email(email_request.text_body)
        confirm_response = self.client.get(invite_path)
        self.assertEqual(confirm_response.status_code, 302)

        set_password_path = confirm_response["Location"]
        password_response = self.client.post(
            set_password_path,
            {
                "new_password1": "InvitePass123!",
                "new_password2": "InvitePass123!",
            },
        )
        self.assertEqual(password_response.status_code, 302)
        self.assertEqual(
            password_response["Location"],
            reverse("staff_invite_complete"),
        )

        invited_user.refresh_from_db()
        self.assertTrue(invited_user.has_usable_password())
        self.client.logout()
        self.assertTrue(
            self.client.login(
                username=invited_user.username,
                password="InvitePass123!",
            )
        )

        reused_link_response = Client().get(invite_path)
        self.assertEqual(reused_link_response.status_code, 200)
        self.assertContains(reused_link_response, "Invite Link Expired")

    @patch("mittab.apps.tab.staff_invites.EmailService")
    def test_invite_resends_for_existing_staff_account(self, email_service):
        email_service.return_value.send_bulk.return_value = 1
        existing_user = get_user_model().objects.create_superuser(
            username="already_staff",
            password="existing_pass_123",
            email="existing@example.com",
        )

        response = self.client.post(
            reverse("invite_staff"),
            {
                "email": "existing@example.com",
                "username": "already_staff",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            get_user_model().objects.filter(
                email__iexact="existing@example.com",
            ).count(),
            1,
        )
        email_request = email_service.return_value.send_bulk.call_args.args[0][0]
        self.assertEqual(email_request.to_address, existing_user.email)

    @patch("mittab.apps.tab.staff_invites.EmailService")
    def test_invite_rejects_existing_non_staff_account(self, email_service):
        get_user_model().objects.create_user(
            username="entry",
            password="entry_pass_123",
            email="entry@example.com",
        )

        response = self.client.post(
            reverse("invite_staff"),
            {
                "email": "entry@example.com",
                "username": "entry",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A non-staff account already uses this email")
        email_service.return_value.send_bulk.assert_not_called()

    @patch("mittab.apps.tab.staff_invites.EmailService")
    def test_invite_rejects_existing_username_for_different_email(
            self, email_service):
        get_user_model().objects.create_user(
            username="taken_username",
            password="entry_pass_123",
            email="taken@example.com",
        )

        response = self.client.post(
            reverse("invite_staff"),
            {
                "email": "different@example.com",
                "username": "taken_username",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A different account already uses this username")
        email_service.return_value.send_bulk.assert_not_called()

    def test_tournament_todo_links_to_staff_invites(self):
        steps = get_tournament_todo_steps()
        invite_step = next(
            step for step in steps if step["slug"] == "add_tab_user_accounts"
        )
        self.assertEqual(invite_step["url"], reverse("invite_staff"))

    def test_invite_page_requires_superuser(self):
        self.client.logout()
        response = self.client.get(reverse("invite_staff"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response["Location"]).path, "/public/login/")

        get_user_model().objects.create_user(
            username="regular",
            password="regular_pass_123",
            email="regular@example.com",
        )
        self.client.login(username="regular", password="regular_pass_123")
        response = self.client.get(reverse("invite_staff"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response["Location"]).path, "/403/")

    def _invite_path_from_email(self, text_body):
        match = re.search(r"https?://[^\s]+", text_body)
        self.assertIsNotNone(match)
        return urlparse(match.group(0)).path
