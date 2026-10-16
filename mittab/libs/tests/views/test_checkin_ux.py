from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.tab.models import CheckIn, Judge, School, TabSettings


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
