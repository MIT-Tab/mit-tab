import pytest
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from mittab.apps.tab.models import (
    Room, TabSettings, Team, Judge, School, Debater
)


@pytest.mark.django_db(transaction=True)
class TestPostOperations(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username='testuser',
            password='testpass123',
            email='test@test.com'
        )
        self.client.login(username='testuser', password='testpass123')
        TabSettings.set("cur_round", 2)

    def test_create_operations(self):
        school = School.objects.first()
        debaters = Debater.objects.all()[:2]
        judge = Judge.objects.first()
        team = Team.objects.first()
        room = Room.objects.first()

        operations_to_test = [
            (reverse("enter_school"), {"name": "Test University"}),
            (reverse("enter_room"), {"name": "Test Room 101", "rank": 1.0}),
            (reverse("enter_judge"),
             {"name": "Test Judge", "rank": 2.0, "schools": [school.id]}),
            (reverse("enter_debater"),
             {"name": "Test Debater", "novice_status": 0, "school": school.id}),
            (reverse("enter_team"),
             {"name": "Test Team", "school": school.id,
              "debaters": [d.id for d in debaters], "seed": 5}),
            (reverse("add_scratch"),
             {"judge": judge.id, "team": team.id, "scratch_type": 0}),
            (reverse("toggle_pairing_released"), {}),
            (reverse("enter_room"), {"name": room.name, "rank": 1.0}),
            (reverse("enter_judge"), {"name": "", "rank": -1}),
            (reverse("enter_debater"), {"name": ""}),
            (reverse("enter_team"), {"name": ""}),
            (reverse("settings_form"),
             {"tot_rounds": 5, "lenient_late": 0, "cur_round": 2}),
            (reverse("settings_form"),
             {"tot_rounds": -1}),
            (reverse("upload_data"), {}),
        ]

        for url, data in operations_to_test:
            response = self.client.post(url, data)
            self.assertIn(response.status_code, [200, 302, 400],
                f"Unexpected status {response.status_code} for POST to {url}")

    def test_action_operations(self):
        TabSettings.set("cur_round", 3)
        school = School.objects.create(name="School To Delete")
        judges = Judge.objects.all()[:3]
        teams = Team.objects.all()[:3]
        rooms = Room.objects.all()[:2]
        room = Room.objects.first()

        operations_to_test = [
            ("POST", reverse("assign_judges"), {}),
            ("POST", reverse("assign_rooms_to_pairing"), {}),
            ("POST", reverse("re_pair_round"), {}),
            ("POST", reverse("pair_round"), {}),
            ("POST", reverse("delete_school", args=[school.id]), {}),
            ("POST", reverse("delete_school", args=[99999]), {}),
            ("POST", reverse("start_tourny"), {}),
            ("POST", reverse("bulk_check_in"), {
                "judges": [str(j.id) for j in judges],
                "teams": [str(t.id) for t in teams],
                "rooms": [str(r.id) for r in rooms],
            }),
            ("POST", reverse("manage_room_tags"),
             {f"room_{room.id}_tags": "test-tag,another-tag"}),
            ("POST", reverse("toggle_pairing_released"), {}),
            ("GET", reverse("manual_backup"), {}),
            ("GET", reverse("export_pairings_csv"), {}),
        ]

        for method, url, data in operations_to_test:
            if method == "POST":
                response = self.client.post(url, data)
            else:
                response = self.client.get(url)
            self.assertIn(response.status_code, [200, 302],
                f"Unexpected status {response.status_code} for {method} to {url}")
