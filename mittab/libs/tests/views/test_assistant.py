"""Exercise the assistant's permissions, tool scope, and provider fallback."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from requests import Timeout

from mittab.apps.tab import assistant
from mittab.apps.tab.assistant import AssistantUnavailable
from mittab.apps.tab.assistant_live import find_roster, get_round, get_setting
from mittab.apps.tab.assistant_sources import read_source, search_source
from mittab.apps.tab.auth_roles import APDA_BOARD_GROUP_NAME, PRESET_TAB_ASSISTANT
from mittab.apps.tab.auth_roles import apply_staff_permission_preset
from mittab.apps.tab.models import (
    CheckIn, Judge, Outround, Room, Round, School, TabSettings, Team,
)
from mittab.settings import filter_error_event


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
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
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

    @override_settings(MITTAB_ASSISTANT_API_TOKEN="", MITTAB_SOURCE_REVISION="")
    def test_local_search_when_provider_is_unconfigured(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Search code and docs")
        self.assertNotContains(page, 'value="ask"')
        with patch("mittab.apps.tab.assistant.requests.post") as send:
            response = self.client.post(
                self.url, {"question": "half point speaks", "mode": "search"},
            )
            fallback = self.client.post(
                self.url, {"question": "half point speaks", "mode": "ask"},
            )
        send.assert_not_called()
        sources = response.json()["sources"]
        self.assertIn("settings/speaks.yaml", [source["path"] for source in sources])
        self.assertTrue(all(not source["url"] for source in sources))
        self.assertEqual(fallback.json()["sources"], sources)

    def test_invalid_request_is_rejected_before_inference(self):
        for data in ({"question": "?"}, {"question": "hello", "history": "x" * 2401}):
            with self.subTest(data_length=len(str(data))):
                response = self.client.post(self.url, {**data, "mode": "ask"})
                self.assertEqual(response.status_code, 400)

    def test_round_tools_read_current_state_and_preserve_field_boundaries(self):
        school = School.objects.create(name="Fixture College")
        gov = Team.objects.create(name="Fixture Gov", school=school, seed=0)
        opp = Team.objects.create(
            name="Fixture Opp", school=school, seed=0, checked_in=False,
        )
        self.assertEqual(find_roster("teams", "Fixture Opp"), {
            "teams": [{"name": opp.name, "checked_in": False}],
        })
        chair = Judge.objects.create(name="Fixture Chair", rank=2)
        room = Room.objects.create(name="Room 1", rank=1)
        pairing = Round.objects.create(
            round_number=1, gov_team=gov, opp_team=opp, chair=chair, room=room,
        )
        pairing.judges.add(chair)
        Outround.objects.create(num_teams=2, gov_team=gov, opp_team=opp)
        slot = get_round(1)
        self.assertEqual(slot["missing_outcome_rooms"], [room.name])
        self.assertEqual(slot["pairings"], [{
            "gov": gov.name, "opp": opp.name, "room": room.name, "chair": chair.name,
            "judges": [chair.name], "outcome": "UNKNOWN",
        }])
        Round.objects.filter(pk=pairing.pk).update(victor=Round.GOV)
        self.assertEqual(get_round(1)["missing_outcome_count"], 0)
        self.assertEqual(get_round(2, "varsity")["total_pairings"], 1)
        self.assertEqual(get_round(2, "novice")["total_pairings"], 0)
        self.assertEqual(find_roster("judges", "Fixture"), {
            "judges": [{"name": chair.name}],
        })
        self.assertIn("error", find_roster("judges", "Fixture", round_number=0))


@override_settings(
    MITTAB_ASSISTANT_ACCOUNT_ID="a" * 32, MITTAB_ASSISTANT_API_TOKEN="test",
)
class TestAssistantProvider(SimpleTestCase):
    @patch("mittab.apps.tab.assistant.requests.post")
    def test_provider_failure_modes(self, send):
        send.return_value.status_code = 429
        with self.assertRaises(AssistantUnavailable):
            assistant.answer("Which setting controls speaks?", "")
        send.return_value.status_code = 200
        send.return_value.json.return_value = {
            "choices": [{"finish_reason": "length", "message": {"content": "Partial"}}],
        }
        with self.assertRaises(AssistantUnavailable):
            assistant.answer("Which setting controls speaks?", "")
        for message in ({"content": ""}, {"tool_calls": [{}]}):
            with self.subTest(message=message):
                send.return_value.json.return_value = {
                    "choices": [{"message": message}],
                }
                with self.assertRaises(AssistantUnavailable):
                    assistant.answer("Which setting controls speaks?", "")
        send.side_effect = Timeout
        with self.assertRaises(AssistantUnavailable):
            assistant.answer("Which setting controls speaks?", "")

    @patch("mittab.apps.tab.assistant.requests.post")
    def test_tools_and_conversation_are_bounded(self, send):
        calls = [
            {"id": name, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments),
            }}
            for name, arguments in (
                ("search_source", {"query": "decimal speaks"}),
                ("read_source", {"path": "settings/speaks.yaml", "start_line": 25}),
                ("delete_round", {"round_number": 1}),
            )
        ]
        send.return_value.status_code = 200
        send.return_value.json.return_value = {
            "choices": [{"message": {"tool_calls": calls}}],
        }
        with self.assertRaises(AssistantUnavailable):
            assistant.answer("Which setting controls speaks?", "Earlier question")
        self.assertEqual(send.call_count, 3)
        final_payload = send.call_args.kwargs["json"]
        self.assertNotIn("tools", final_payload)
        self.assertIn("Earlier question", final_payload["messages"][1]["content"])
        self.assertEqual(
            json.loads(final_payload["messages"][-1]["content"]),
            {"error": "Unknown read tool."},
        )

    def test_invalid_tool_inputs(self):
        for name, arguments in (
            ("get_setting", "[1]"),
            ("get_setting", "malformed JSON"),
            ("get_setting", "{}"),
        ):
            with self.subTest(arguments=arguments):
                result = assistant._run_tool({  # pylint: disable=protected-access
                    "name": name, "arguments": arguments,
                })
                self.assertIn("error", result)
        self.assertIn("error", get_setting("private_token"))
        self.assertIn("error", get_round(0))
        self.assertIn("error", get_round(1, "unknown"))
        self.assertIn("error", find_roster("teams", "x"))
        self.assertIn("error", find_roster("unknown", "name"))


class TestAssistantSources(SimpleTestCase):
    def test_local_files_reject_hidden_and_symlinked_paths(self):
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            docs = Path(directory) / "docs"
            docs.mkdir()
            (docs / "help.md").write_text("Half points work.\n", encoding="utf-8")
            (docs / ".private.md").write_text("Private value\n", encoding="utf-8")
            (docs / "alias.md").symlink_to(docs / "help.md")
            self.assertEqual([match["path"] for match in search_source("half")], [
                "docs/help.md",
            ])
            self.assertIn("error", read_source("docs/.private.md"))
            self.assertIn("error", read_source("docs/alias.md"))
            self.assertIn("error", read_source("docs/help.md", start_line=2))
            self.assertEqual(search_source("where is the"), [])
            self.assertEqual(search_source(None), [])

    def test_error_filter_drops_only_assistant_requests(self):
        assistant_request = {"request": {"url": "https://example.com/ask-mittab/"}}
        unrelated_request = {"request": {"url": "https://example.com/setup/"}}
        self.assertIsNone(filter_error_event(assistant_request, {}))
        self.assertIs(filter_error_event(unrelated_request, {}), unrelated_request)
        self.assertEqual(filter_error_event({}, {}), {})
