import pytest
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from mittab.apps.tab.models import (
    PUBLIC_HOME_PAGE_DEFINITIONS,
    Room,
    TabSettings,
    Team,
    Judge,
    School,
    Debater,
    PublicHomePage,
    PublicHomeShortcut,
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

    def test_public_home_shortcuts_update(self):
        response = self.client.post(
            reverse("public_home_shortcuts"),
            {
                "tournament_name": "MIT Invitational",
                "slot_1": "public_team_results",
                "slot_2": "public_motions",
                "slot_3": "public_ballots",
                "slot_4": "released_pairings",
                "slot_5": "missing_ballots",
                "slot_6": "varsity_outrounds",
                "slot_7": "novice_outrounds",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            PublicHomeShortcut.objects.get(position=1).nav_item,
            "public_team_results",
        )
        self.assertEqual(
            PublicHomeShortcut.objects.get(position=2).nav_item,
            "public_motions",
        )
        self.assertEqual(
            PublicHomeShortcut.objects.get(position=3).nav_item,
            "public_ballots",
        )
        self.assertEqual(TabSettings.get("tournament_name"), "MIT Invitational")

    def test_start_new_tourny_clears_homepage_configuration(self):
        PublicHomePage.ensure_defaults()
        PublicHomeShortcut.objects.update_or_create(
            position=1,
            defaults={"nav_item": "public_team_results"},
        )
        PublicHomePage.objects.update_or_create(
            slug="custom_link",
            defaults={
                "title": "Custom Link",
                "subtitle": "Custom subtitle",
                "url_path": "/public/custom-link/",
                "sort_order": 99,
                "is_active": True,
            },
        )

        response = self.client.post(reverse("start_tourny"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PublicHomeShortcut.objects.count(), 0)
        self.assertEqual(PublicHomePage.objects.count(), 0)

    def test_public_home_page_ensure_defaults_is_idempotent(self):
        PublicHomePage.objects.all().delete()
        PublicHomePage.objects.create(
            slug="released_pairings",
            title="Custom Released Pairings",
            subtitle="custom subtitle",
            url_path="/public/pairings/",
            sort_order=15,
        )

        PublicHomePage.ensure_defaults()
        PublicHomePage.ensure_defaults()

        self.assertEqual(
            PublicHomePage.objects.count(),
            len(PUBLIC_HOME_PAGE_DEFINITIONS),
        )
        self.assertEqual(
            PublicHomePage.objects.values_list("slug", flat=True).distinct().count(),
            len(PUBLIC_HOME_PAGE_DEFINITIONS),
        )
        self.assertEqual(
            PublicHomePage.objects.get(slug="released_pairings").title,
            "Custom Released Pairings",
        )
