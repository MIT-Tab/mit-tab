import pytest
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from mittab.apps.tab.models import (
    Room, TabSettings, Team, Judge, School, Debater
)
from mittab.apps.tab.public_rankings import set_standings_publication_setting


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
        set_standings_publication_setting("speaker_results", True)
        set_standings_publication_setting("team_results", True)

    def test_create_operations(self):
        school = School.objects.first()
        debaters = Debater.objects.all()[:2]
        judge = Judge.objects.first()
        team = Team.objects.first()
        room = Room.objects.first()

        operations_to_test = [
            ("enter_school", reverse("enter_school"),
             {"name": "Test University"}),
            ("enter_room", reverse("enter_room"),
             {"name": "Test Room 101", "rank": 1.0}),
            ("enter_judge", reverse("enter_judge"),
             {"name": "Test Judge", "rank": 2.0, "schools": [school.id]}),
            ("enter_debater", reverse("enter_debater"),
             {"name": "Test Debater", "novice_status": 0, "school": school.id}),
            ("enter_team", reverse("enter_team"),
             {"name": "Test Team", "school": school.id,
              "debaters": [d.id for d in debaters], "seed": 5}),
            ("add_scratch", reverse("add_scratch"),
             {"judge": judge.id, "team": team.id, "scratch_type": 0}),
            ("toggle_pairing_released",
             reverse("toggle_pairing_released"), {}),
            ("enter_room_duplicate", reverse("enter_room"),
             {"name": room.name, "rank": 1.0}),
            ("enter_judge_invalid", reverse("enter_judge"),
             {"name": "", "rank": -1}),
            ("enter_debater_invalid", reverse("enter_debater"),
             {"name": ""}),
            ("enter_team_invalid", reverse("enter_team"),
             {"name": ""}),
            ("settings_form", reverse("settings_form"),
             {"tot_rounds": 5, "lenient_late": 0, "cur_round": 2}),
            ("settings_form_invalid", reverse("settings_form"),
             {"tot_rounds": -1}),
            ("upload_data_empty", reverse("upload_data"), {}),
        ]

        failures = []
        for name, url, data in operations_to_test:
            response = self.client.post(url, data, follow=True)
            # Check final status after following redirects
            if response.status_code != 200:
                failures.append(
                    f"{name}: {response.status_code} "
                    f"(expected 200 after redirects) for POST to {url}")


        self.assertEqual([], failures, "Failed operations:\n" + "\n".join(failures))

    def test_action_operations(self):
        TabSettings.set("cur_round", 3)
        school = School.objects.create(name="School To Delete")
        judges = Judge.objects.all()[:3]
        teams = Team.objects.all()[:3]
        rooms = Room.objects.all()[:2]
        room = Room.objects.first()

        operations_to_test = [
            ("assign_judges", "POST", reverse("assign_judges"), {}),
            ("assign_rooms_to_pairing", "POST",
             reverse("assign_rooms_to_pairing"), {}),
            ("re_pair_round", "POST", reverse("re_pair_round"), {}),
            ("pair_round", "POST", reverse("pair_round"), {}),
            ("delete_school_valid", "POST",
             reverse("delete_school", args=[school.id]), {}),
            ("delete_school_invalid", "POST",
             reverse("delete_school", args=[99999]), {}),
            ("start_tourny", "POST", reverse("start_tourny"), {}),
            ("bulk_check_in", "POST", reverse("bulk_check_in"), {
                "judges": [str(j.id) for j in judges],
                "teams": [str(t.id) for t in teams],
                "rooms": [str(r.id) for r in rooms],
            }),
            ("manage_room_tags", "POST", reverse("manage_room_tags"),
             {f"room_{room.id}_tags": "test-tag,another-tag"}),
            ("toggle_pairing_released", "POST", reverse("toggle_pairing_released"), {}),
            ("manual_backup", "GET", reverse("manual_backup"), {}),
            ("export_pairings_csv", "GET", reverse("export_pairings_csv"), {}),
        ]

        failures = []
        for name, method, url, data in operations_to_test:
            if method == "POST":
                response = self.client.post(url, data, follow=True)
            else:
                response = self.client.get(url, follow=True)

            # Check final status after following redirects
            if response.status_code != 200:
                failures.append(
                    f"{name}: {response.status_code} "
                    f"(expected 200 after redirects) for {method} to {url}")

        self.assertEqual([], failures, "Failed operations:\n" + "\n".join(failures))

    def test_debater_counts_api_permission_flow(self):
        response = self.client.get(reverse("debater_counts_api"))
        self.assertEqual(response.status_code, 200)

        set_standings_publication_setting("speaker_results", False)
        set_standings_publication_setting("team_results", False)
        response = self.client.get(reverse("debater_counts_api"))
        self.assertEqual(response.status_code, 423)
        set_standings_publication_setting("speaker_results", True)
        set_standings_publication_setting("team_results", True)
