from decimal import Decimal
from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.tab.auth_roles import APDA_BOARD_GROUP_NAME
from mittab.apps.tab.models import (
    CheckIn, Judge, JudgeExpectedCheckIn, Room, RoomCheckIn, School, TabSettings, Team,
)


class CheckinToggleParser(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.toggles = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "checkin-toggle" in attrs.get("class", "").split():
            self.toggles.append(attrs)


@pytest.mark.django_db(transaction=True)
class TestCheckinUx(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="checkin-ux-user",
            password="testpass123",
            email="checkin-ux@example.com",
        )
        self.client.force_login(self.user)
        TabSettings.set("cur_round", 2)

    def test_pair_round_lists_only_judges_checked_in_for_current_round(self):
        school = School.objects.create(name="Roster Test School")
        current_judge = Judge.objects.create(
            name="A Current Round Judge",
            rank=Decimal("2.00"),
        )
        current_judge.schools.add(school)
        other_judge = Judge.objects.create(
            name="Z Other Round Judge",
            rank=Decimal("2.00"),
        )
        CheckIn.objects.create(judge=current_judge, round_number=2)
        CheckIn.objects.create(judge=other_judge, round_number=1)

        response = self.client.get(reverse("pair_round"))

        self.assertEqual(response.status_code, 200)
        listed_judges = list(response.context["checked_in_judges"])
        self.assertIn(current_judge, listed_judges)
        self.assertNotIn(other_judge, listed_judges)
        self.assertContains(response, "Checked-in judges")
        self.assertContains(response, current_judge.name)
        self.assertContains(response, school.name)
        self.assertNotContains(response, other_judge.name)

    def test_batch_checkin_places_outrounds_last_and_uses_buttons(self):
        judge = Judge.objects.create(
            name="Round Ordering Judge",
            rank=Decimal("2.00"),
        )
        CheckIn.objects.create(judge=judge, round_number=2)
        CheckIn.objects.create(judge=judge, round_number=0)

        response = self.client.get(reverse("batch_checkin"))

        self.assertEqual(response.status_code, 200)
        judge_row = next(
            row for row in response.context["judge_data"]
            if row["entity"] == judge
        )
        expected_checkins = [
            round_number in {0, 2}
            for round_number in range(1, TabSettings.get("tot_rounds") + 1)
        ] + [True]
        self.assertEqual(judge_row["checkins"], expected_checkins)
        content = response.content.decode()
        self.assertLess(content.index("Round 1"), content.index("Outrounds"))
        self.assertContains(response, '<button type="button" class="bulk-toggle')
        self.assertNotContains(response, '<a href="#" class="bulk-toggle')

    def test_displayed_rounds_save_and_reload_in_the_same_columns(self):
        judge = Judge.objects.create(name="Batch Round Judge", rank=Decimal("2"))
        room = Room.objects.create(name="Batch Round Room", rank=1)
        round_numbers = list(range(1, TabSettings.get("tot_rounds") + 1)) + [0]

        for entity_type, entity, model, field in (
            ("judge", judge, CheckIn, "judge"),
            ("judge_expected", judge, JudgeExpectedCheckIn, "judge"),
            ("room", room, RoomCheckIn, "room"),
        ):
            for column, expected_round in enumerate(round_numbers):
                with self.subTest(entity_type=entity_type, round_number=expected_round):
                    response = self.client.get(reverse("batch_checkin"))
                    toggles = [
                        toggle for toggle in CheckinToggleParser(
                            response.content.decode()
                        ).toggles
                        if toggle["id"].startswith(f"toggle-{entity_type}-{entity.id}-")
                    ]
                    self.assertEqual(
                        [int(toggle["data-round"]) for toggle in toggles], round_numbers
                    )
                    payload = {
                        "entity_type": entity_type,
                        "action": "check_in",
                        "entity_ids[]": [entity.id],
                        "rounds[]": [toggles[column]["data-round"]],
                    }
                    response = self.client.post(reverse("bulk_check_in"), payload)
                    self.assertEqual(response.json(), {"success": True})
                    self.assertEqual(
                        list(model.objects.filter(**{field: entity}).values_list(
                            "round_number", flat=True
                        )), [expected_round]
                    )

                    response = self.client.get(reverse("batch_checkin"))
                    saved_toggles = [
                        toggle for toggle in CheckinToggleParser(
                            response.content.decode()
                        ).toggles
                        if toggle["id"].startswith(f"toggle-{entity_type}-{entity.id}-")
                    ]
                    self.assertEqual(
                        ["checked" in toggle for toggle in saved_toggles],
                        [rn == expected_round for rn in round_numbers],
                    )

                    payload["action"] = "check_out"
                    response = self.client.post(reverse("bulk_check_in"), payload)
                    self.assertEqual(response.json(), {"success": True})
                    self.assertFalse(model.objects.filter(**{field: entity}).exists())

    def test_bulk_undo_restores_only_changed_cells_from_another_session(self):
        original = Judge.objects.create(name="Originally available", rank=Decimal("2"))
        other = Judge.objects.create(name="Originally unavailable", rank=Decimal("2"))
        endpoint = reverse("bulk_check_in")
        second_device = Client()
        second_device.force_login(self.user)

        for entity_type, model in (
            ("judge", CheckIn), ("judge_expected", JudgeExpectedCheckIn),
        ):
            with self.subTest(entity_type=entity_type):
                model.objects.create(judge=original, round_number=5)
                model.objects.create(judge=other, round_number=4)
                payload = {
                    "entity_type": entity_type,
                    "entity_ids[]": [original.pk, other.pk],
                    "rounds[]": [5],
                    "action": "check_in",
                    "include_undo": True,
                }
                change = self.client.post(endpoint, payload).json()["undo"]
                self.assertEqual(change["count"], 1)
                retry = self.client.post(endpoint, payload).json()["undo"]
                self.assertEqual(retry, change)
                page = second_device.get(reverse("batch_checkin"))
                self.assertContains(page, change["id"])

                undo = {
                    "entity_type": entity_type,
                    "action": "undo",
                    "change_id": change["id"],
                }
                self.assertEqual(second_device.post(endpoint, undo).status_code, 200)
                self.assertEqual(second_device.post(endpoint, undo).status_code, 409)
                self.assertSetEqual(
                    set(model.objects.filter(judge__in=[original, other]).values_list(
                        "judge_id", "round_number",
                    )),
                    {(original.pk, 5), (other.pk, 4)},
                )

                payload["action"] = "check_out"
                cleared = self.client.post(endpoint, payload).json()["undo"]
                undo["change_id"] = cleared["id"]
                self.assertEqual(second_device.post(endpoint, undo).status_code, 200)
                round_five = model.objects.filter(
                    judge__in=[original, other], round_number=5,
                )
                self.assertEqual(
                    list(round_five.values_list("judge_id", flat=True)), [original.pk],
                )

    def test_bulk_undo_rejects_stale_requests_and_conflicting_edits(self):
        room = Room.objects.create(name="Undo Room", rank=1)
        endpoint = reverse("bulk_check_in")
        payload = {
            "entity_type": "room",
            "entity_ids[]": [room.pk],
            "rounds[]": [5],
            "action": "check_in",
            "include_undo": True,
        }
        first = self.client.post(endpoint, payload).json()["undo"]["id"]
        payload["rounds[]"] = [4]
        second = self.client.post(endpoint, payload).json()["undo"]["id"]

        undo = {"entity_type": "room", "action": "undo", "change_id": first}
        self.assertEqual(self.client.post(endpoint, undo).status_code, 409)
        undo["change_id"] = second
        self.assertEqual(self.client.post(endpoint, undo).status_code, 200)
        RoomCheckIn.objects.filter(room=room, round_number=5).delete()
        undo["change_id"] = first
        self.assertEqual(self.client.post(endpoint, undo).status_code, 409)
        self.assertFalse(RoomCheckIn.objects.filter(room=room).exists())


    def test_team_bulk_undo_is_unavailable_to_board(self):
        teams = list(Team.objects.order_by("pk")[:2])
        Team.objects.filter(pk=teams[0].pk).update(checked_in=True)
        Team.objects.filter(pk=teams[1].pk).update(checked_in=False)
        endpoint = reverse("bulk_check_in")
        change = self.client.post(endpoint, {
            "entity_type": "team",
            "entity_ids[]": [team.pk for team in teams],
            "action": "check_out",
            "include_undo": True,
        }).json()["undo"]

        board = get_user_model().objects.create_user(username="checkin-board")
        group, _ = Group.objects.get_or_create(name=APDA_BOARD_GROUP_NAME)
        board.groups.add(group)
        board_client = Client()
        board_client.force_login(board)
        undo = {"entity_type": "team", "action": "undo", "change_id": change["id"]}
        self.assertIn(board_client.post(endpoint, undo).status_code, {302, 403})
        teams[0].refresh_from_db()
        self.assertFalse(teams[0].checked_in)

        self.assertEqual(self.client.post(endpoint, undo).status_code, 200)
        for team in teams:
            team.refresh_from_db()
        self.assertEqual([team.checked_in for team in teams], [True, False])
