import json
import os
import tempfile
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.tab.models import (
    DEFAULT_TOURNAMENT_NAME,
    TabSettings,
    UserTournamentSetupPreference,
)
from mittab.libs.tournament_todo import get_tournament_todo_steps
from mittab.libs.tournament_todo_rules import get_required_settings_category_ids


@pytest.mark.django_db(transaction=True)
class TestTournamentTodo(TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="todo_user",
            password="todo_pass_123",
            email="todo@test.com",
        )

    def test_login_redirects_to_todo_by_default(self):
        response = self.client.post(
            reverse("tab_login"),
            {"username": "todo_user", "password": "todo_pass_123"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("tournament_todo"))

    def test_login_respects_opt_out_preference(self):
        UserTournamentSetupPreference.objects.create(
            user=self.user,
            hide_tournament_todo=True,
        )
        response = self.client.post(
            reverse("tab_login"),
            {"username": "todo_user", "password": "todo_pass_123"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("index"))

    def test_todo_post_updates_preference_and_step(self):
        self.client.login(username="todo_user", password="todo_pass_123")
        response = self.client.post(
            reverse("tournament_todo"),
            {
                "step_set_tournament_name": "on",
                "hide_tournament_todo": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TabSettings.get("todo_set_tournament_name", 0), 1)

        preference = UserTournamentSetupPreference.objects.get(user=self.user)
        self.assertTrue(preference.hide_tournament_todo)

    def test_tournament_name_step_auto_completes(self):
        self.client.login(username="todo_user", password="todo_pass_123")
        TabSettings.set("tournament_name", "MIT Spring Invitational")

        response = self.client.get(reverse("tournament_todo"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TabSettings.get("todo_set_tournament_name", 0), 1)

    def test_todo_toggle_updates_step_via_ajax(self):
        self.client.login(username="todo_user", password="todo_pass_123")

        response = self.client.post(
            reverse("tournament_todo_toggle"),
            {
                "field": "step_set_tournament_name",
                "checked": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["checked"])
        self.assertEqual(payload["phase"], "before_tournament")
        self.assertIn("section_completed", payload)
        self.assertIn("section_total", payload)
        self.assertIn("section_progress_percent", payload)
        self.assertEqual(TabSettings.get("todo_set_tournament_name", 0), 1)

    def test_todo_toggle_unchecks_step_via_ajax(self):
        self.client.login(username="todo_user", password="todo_pass_123")
        TabSettings.set("todo_set_tournament_name", 1)

        response = self.client.post(
            reverse("tournament_todo_toggle"),
            {
                "field": "step_set_tournament_name",
                "checked": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["checked"])
        self.assertEqual(payload["phase"], "before_tournament")
        self.assertEqual(TabSettings.get("todo_set_tournament_name", 0), 0)

    def test_todo_toggle_updates_preference_via_ajax(self):
        self.client.login(username="todo_user", password="todo_pass_123")

        response = self.client.post(
            reverse("tournament_todo_toggle"),
            {
                "field": "hide_tournament_todo",
                "checked": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["checked"])

        preference = UserTournamentSetupPreference.objects.get(user=self.user)
        self.assertTrue(preference.hide_tournament_todo)

    def test_todo_toggle_rejects_unknown_field(self):
        self.client.login(username="todo_user", password="todo_pass_123")

        response = self.client.post(
            reverse("tournament_todo_toggle"),
            {
                "field": "step_nope",
                "checked": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)

    def test_get_tournament_todo_steps_ignores_invalid_url_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "tournament_todo_steps.yaml")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "before_tournament_steps:\n"
                    "  - slug: missing_url\n"
                    "    title: Missing URL\n"
                    "    url_name: not_a_real_route\n"
                )

            with patch(
                "mittab.libs.tournament_todo.TOURNAMENT_TODO_CONFIG_PATH",
                config_path,
            ):
                steps = get_tournament_todo_steps()

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["slug"], "missing_url")
        self.assertEqual(steps[0]["url"], "")

    def test_get_tournament_todo_steps_ignores_invalid_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "tournament_todo_steps.yaml")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write("before_tournament_steps: [\n")

            with patch(
                "mittab.libs.tournament_todo.TOURNAMENT_TODO_CONFIG_PATH",
                config_path,
            ):
                steps = get_tournament_todo_steps()

        self.assertEqual(steps, [])

    def test_required_settings_categories_ignores_invalid_and_non_yaml_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_dir = os.path.join(tmpdir, "settings")
            os.makedirs(settings_dir, exist_ok=True)

            with open(
                os.path.join(settings_dir, "valid.yaml"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(
                    "category:\n"
                    "  id: core\n"
                )
            with open(
                os.path.join(settings_dir, "broken.yaml"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("category: [\n")
            with open(
                os.path.join(settings_dir, "notes.txt"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("category:\n  id: should_not_count\n")

            with self.settings(BASE_DIR=tmpdir):
                category_ids = get_required_settings_category_ids()

        self.assertEqual(category_ids, {"core"})

    def test_todo_post_parses_checkbox_values(self):
        self.client.login(username="todo_user", password="todo_pass_123")
        TabSettings.set("tournament_name", DEFAULT_TOURNAMENT_NAME)

        response = self.client.post(
            reverse("tournament_todo"),
            {
                "step_set_tournament_name": "0",
                "hide_tournament_todo": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TabSettings.get("todo_set_tournament_name", 0), 0)
        preference = UserTournamentSetupPreference.objects.get(user=self.user)
        self.assertFalse(preference.hide_tournament_todo)

    def test_todo_description_is_escaped_in_template(self):
        self.client.login(username="todo_user", password="todo_pass_123")
        injected_description = "<script>alert('xss')</script>"
        mocked_steps = [
            {
                "slug": "unsafe_desc",
                "title": "Unsafe Description",
                "description": injected_description,
                "url": "",
                "checked": False,
                "auto_completed": False,
                "phase": "before_tournament",
            },
        ]

        with patch(
            "mittab.apps.tab.views.tournament_todo_views.get_tournament_todo_steps",
            return_value=mocked_steps,
        ):
            response = self.client.get(reverse("tournament_todo"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", content)
        self.assertNotIn(injected_description, content)
        self.assertNotIn('data-html="true"', content)
