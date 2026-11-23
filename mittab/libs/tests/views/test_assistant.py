"""Exercise the assistant's permissions, tool scope, and provider fallback."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from mittab.apps.tab.assistant import AssistantUnavailable
from mittab.apps.tab.assistant_live import find_roster
from mittab.apps.tab.assistant_sources import read_source
from mittab.apps.tab.auth_roles import APDA_BOARD_GROUP_NAME, PRESET_TAB_ASSISTANT
from mittab.apps.tab.auth_roles import apply_staff_permission_preset
from mittab.apps.tab.models import CheckIn, Judge, TabSettings


@override_settings(
    MITTAB_ASSISTANT_ENABLED=True,
    MITTAB_ASSISTANT_ACCOUNT_ID="a" * 32,
    MITTAB_ASSISTANT_API_TOKEN="test-token",
    MITTAB_SOURCE_REVISION="b" * 40,
)
class TestAssistant(TestCase):
    def setUp(self):
        self.url = reverse("ask_mittab")
        self.admin = get_user_model().objects.create_superuser("full-staff")
        self.client.force_login(self.admin)

    def test_only_full_staff_can_call_assistant(self):
        user = get_user_model().objects.create_user("limited-staff")
        apply_staff_permission_preset(user, PRESET_TAB_ASSISTANT)
        self.client.force_login(user)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        group, _ = Group.objects.get_or_create(name=APDA_BOARD_GROUP_NAME)
        self.admin.groups.add(group)
        self.client.force_login(self.admin)
        with patch(
            "mittab.apps.tab.middleware.is_apda_board_access_open", return_value=True,
        ):
            with patch("mittab.apps.tab.assistant.requests.post") as send:
                response = self.client.post(
                    self.url, {"question": "Show judges", "mode": "ask"},
                )
        self.assertNotEqual(response.status_code, 200)
        send.assert_not_called()

    @override_settings(MITTAB_ASSISTANT_ENABLED=False)
    def test_disabled_assistant_is_hidden(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_provider_failure_returns_local_sources(self):
        with patch(
            "mittab.apps.tab.assistant.answer", side_effect=AssistantUnavailable,
        ):
            response = self.client.post(
                self.url, {"question": "half point speaks", "mode": "ask"},
            )
        payload = response.json()
        self.assertIn("Chat is unavailable", payload["message"])
        self.assertTrue(payload["sources"])
        self.assertIn("/blob/" + "b" * 40, payload["sources"][0]["url"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_live_read_is_sent_as_a_tool_result(self):
        TabSettings.set("decimal_speaks", 1)
        tool_call = {
            "id": "test_call",
            "type": "function",
            "function": {
                "name": "get_setting",
                "arguments": '{"key":"decimal_speaks"}',
            },
        }
        with patch("mittab.apps.tab.assistant.requests.post") as send:
            send.return_value.status_code = 200
            send.return_value.json.side_effect = [
                {"choices": [{"message": {"tool_calls": [tool_call]}}]},
                {"choices": [{"message": {"content": "Half points are enabled."}}]},
            ]
            response = self.client.post(
                self.url, {"question": "Allow half points?", "mode": "ask"},
            )
        self.assertEqual(response.json()["answer"], "Half points are enabled.")
        messages = send.call_args.kwargs["json"]["messages"]
        self.assertEqual(
            json.loads(messages[-1]["content"]), {"key": "decimal_speaks", "value": 1},
        )
        self.assertEqual(messages[-1]["tool_call_id"], "test_call")

    def test_protected_source_paths_are_excluded(self):
        for path in ("../.env", "mittab/settings.py", "/etc/passwd"):
            self.assertIn("error", read_source(path))
        self.assertIn("decimal_speaks", read_source("settings/speaks.yaml")["text"])

    def test_judge_read_excludes_non_roster_fields(self):
        judge = Judge.objects.create(
            name="Fixture Judge", rank=2, email="fixture@example.com",
            ballot_code="test-code", wing_only=True,
        )
        CheckIn.objects.create(judge=judge, round_number=2)
        self.assertEqual(find_roster("judges", "Fixture", round_number=2), {
            "round_number": 2,
            "judges": [{"name": "Fixture Judge", "checked_in": True}],
        })
