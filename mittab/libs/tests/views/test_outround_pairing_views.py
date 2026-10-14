import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.tab.models import Outround, TabSettings


@pytest.mark.django_db(transaction=True)
class TestOutroundPairingViews(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="outround_test_user",
            password="testpass123",
            email="outround@test.com",
        )
        self.client.login(username="outround_test_user", password="testpass123")

    def test_default_view_uses_enabled_novice_break(self):
        TabSettings.set("var_teams_to_break", 0)
        TabSettings.set("nov_teams_to_break", 4)

        response = self.client.get(reverse("outround_pairing_view_default"))

        self.assertRedirects(
            response,
            reverse("outround_pairing_view", args=[Outround.NOVICE, 4]),
            fetch_redirect_response=False,
        )

    def test_default_view_rejects_disabled_breaks(self):
        TabSettings.set("var_teams_to_break", 0)
        TabSettings.set("nov_teams_to_break", 0)

        response = self.client.get(reverse("outround_pairing_view_default"))

        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_default_view_rejects_negative_break_size(self):
        TabSettings.set("var_teams_to_break", -1)

        response = self.client.get(reverse("outround_pairing_view_default"))

        self.assertRedirects(response, "/", fetch_redirect_response=False)
