import pytest
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.tab.forms import PublicHomepageForm
from mittab.apps.tab.models import TabSettings
from mittab.libs.cacheing import cache_logic


@pytest.mark.django_db(transaction=True)
class TestThemeSettings(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        caches["public"].clear()
        cache_logic.clear_cache()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="testuser",
            password="testpass123",
            email="test@test.com",
        )
        self.client.login(username="testuser", password="testpass123")

    def _shortcut_payload(self):
        return {
            "slot_1": "released_pairings",
            "slot_2": "missing_ballots",
            "slot_3": "submit_e_ballot",
            "slot_4": "judge_list",
            "slot_5": "team_list",
            "slot_6": "varsity_outrounds",
            "slot_7": "novice_outrounds",
        }

    def test_public_homepage_form_saves_theme_color(self):
        payload = {
            "tournament_name": "MIT Invitational",
            "theme_color": "#A14BC0",
            **self._shortcut_payload(),
        }

        form = PublicHomepageForm(payload)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertEqual(TabSettings.get("theme_color"), "#A14BC0")

    def test_public_homepage_form_rejects_invalid_theme_color(self):
        payload = {
            "tournament_name": "MIT Invitational",
            "theme_color": "#12ZZZZ",
            **self._shortcut_payload(),
        }

        form = PublicHomepageForm(payload)
        self.assertFalse(form.is_valid())
        self.assertIn("theme_color", form.errors)

    def test_theme_css_variables_are_rendered_in_base_template(self):
        TabSettings.set("theme_color", "#A14BC0")
        caches["public"].clear()
        cache_logic.clear_cache()

        response = self.client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn("--theme-color: #A14BC0;", content)
        self.assertIn("--theme-color-rgb: 161, 75, 192;", content)
