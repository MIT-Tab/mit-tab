from decimal import Decimal
from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.tab.models import (
    CheckIn, Judge, JudgeExpectedCheckIn, Room, RoomCheckIn, School, TabSettings,
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
