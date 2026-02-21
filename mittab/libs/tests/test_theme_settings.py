import pytest
from django.core.cache import caches
from django.test import TestCase
from django.urls import reverse

from mittab.apps.tab.forms import SettingsForm
from mittab.apps.tab.models import TabSettings
from mittab.apps.tab.views.views import get_settings_from_yaml
from mittab.libs.cacheing import cache_logic


@pytest.mark.django_db(transaction=True)
class TestThemeSettings(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        caches["public"].clear()
        cache_logic.clear_cache()

    def _build_settings_payload(self, settings):
        payload = {}
        for setting in settings:
            key = f"setting_{setting['name']}"
            setting_type = setting.get("type")
            setting_value = setting.get("value")

            if setting_type == "boolean":
                if setting_value:
                    payload[key] = "on"
                continue

            payload[key] = str(setting_value)

        return payload

    def test_settings_form_saves_theme_color(self):
        yaml_settings, _, _ = get_settings_from_yaml()
        payload = self._build_settings_payload(yaml_settings)
        payload["setting_theme_color"] = "#A14BC0"

        form = SettingsForm(payload, settings=yaml_settings)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertEqual(TabSettings.get("theme_color"), "#A14BC0")

    def test_settings_form_rejects_invalid_theme_color(self):
        yaml_settings, _, _ = get_settings_from_yaml()
        payload = self._build_settings_payload(yaml_settings)
        payload["setting_theme_color"] = "#12ZZZZ"

        form = SettingsForm(payload, settings=yaml_settings)
        self.assertFalse(form.is_valid())
        self.assertIn("setting_theme_color", form.errors)

    def test_theme_css_variables_are_rendered_in_base_template(self):
        TabSettings.set("theme_color", "#A14BC0")
        caches["public"].clear()
        cache_logic.clear_cache()

        response = self.client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn("--theme-color: #A14BC0;", content)
        self.assertIn("--theme-color-rgb: 161, 75, 192;", content)
