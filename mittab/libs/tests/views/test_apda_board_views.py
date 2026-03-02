import pytest

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.tab.auth_roles import APDA_BOARD_GROUP_NAME
from mittab.apps.tab.models import Debater, Round, School, TabSettings
from mittab.apps.tab.public_rankings import (
    get_standings_publication_setting,
    set_standings_publication_setting,
)


@pytest.mark.django_db(transaction=True)
class TestApdaBoardViews(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_user(
            username="apda_board_tester",
            email="apda@example.com",
            password="password",
        )
        group, _ = Group.objects.get_or_create(name=APDA_BOARD_GROUP_NAME)
        self.user.groups.add(group)
        self.client.login(username="apda_board_tester", password="password")

    def test_index_redirects_to_apda_board_home(self):
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("apda_board_home"))

    def test_apda_board_allowed_pages_render(self):
        school = School.objects.first()
        debater = Debater.objects.first()

        urls = [
            reverse("apda_board_home"),
            reverse("apda_board_school_detail", args=[school.id]),
            reverse("apda_board_debater_detail", args=[debater.id]),
            reverse("public_home"),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

    def test_apda_board_blocked_pages_redirect_to_403(self):
        school = School.objects.first()
        blocked_urls = [
            reverse("view_teams"),
            reverse("public_rankings_control"),
            reverse("view_school", args=[school.id]),
        ]

        for url in blocked_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, "/403/")

    def test_apda_board_home_updates_only_standings_published_settings(self):
        set_standings_publication_setting("speaker_results", False)
        set_standings_publication_setting("team_results", True)

        response = self.client.post(
            reverse("apda_board_home"),
            {
                "standings_speaker_results": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("apda_board_home"))
        self.assertTrue(get_standings_publication_setting(
            "speaker_results")["published"])
        self.assertFalse(get_standings_publication_setting("team_results")["published"])

    def test_apda_board_school_detail_updates_only_apda_id(self):
        school = School.objects.first()
        original_name = school.name

        response = self.client.post(
            reverse("apda_board_school_detail", args=[school.id]),
            {
                "apda_id": 24680,
                "name": "Do Not Change",
            },
        )

        self.assertEqual(response.status_code, 302)
        school.refresh_from_db()
        self.assertEqual(school.apda_id, 24680)
        self.assertEqual(school.name, original_name)

    def test_apda_board_debater_detail_updates_only_apda_id(self):
        debater = Debater.objects.first()
        original_name = debater.name

        response = self.client.post(
            reverse("apda_board_debater_detail", args=[debater.id]),
            {
                "apda_id": 13579,
                "name": "Do Not Change",
            },
        )

        self.assertEqual(response.status_code, 302)
        debater.refresh_from_db()
        self.assertEqual(debater.apda_id, 13579)
        self.assertEqual(debater.name, original_name)

    def test_apda_board_login_allowed_when_final_round_is_paired(self):
        self.client.logout()
        logged_in = self.client.login(
            username="apda_board_tester",
            password="password",
        )
        self.assertTrue(logged_in)

    def test_apda_board_login_denied_before_final_round_is_paired(self):
        total_inrounds = int(TabSettings.get("tot_rounds", 0) or 0)
        Round.objects.filter(round_number=total_inrounds).delete()
        self.client.logout()
        logged_in = self.client.login(
            username="apda_board_tester",
            password="password",
        )
        self.assertFalse(logged_in)
