import pytest
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from nplusone.core import profiler

from mittab.apps.tab.models import (
    Room, TabSettings, Team, Round, Judge, School, Debater, Outround
)


@pytest.mark.django_db(transaction=True)
class TestTabViews(TestCase):
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

        self.novice_outround = Outround(
            gov_team=Team.objects.first(),
            opp_team=Team.objects.last(),
            num_teams=2,
            type_of_round=Outround.NOVICE,
            room=Room.objects.first(),
        )
        self.novice_outround.save()

        self.varsity_outround = Outround(
            gov_team=Team.objects.first(),
            opp_team=Team.objects.last(),
            num_teams=2,
            type_of_round=Outround.VARSITY,
            room=Room.objects.last(),
        )
        self.varsity_outround.save()

    def test_render(self):
        judge = Judge.objects.first()
        team = Team.objects.first()
        debater = Debater.objects.first()
        school = School.objects.first()
        room = Room.objects.first()
        round_obj = Round.objects.filter(round_number=1).first()
        outround = Outround.objects.first()

        views_to_test = [
            (reverse("index"), "Schools"),
            (reverse("view_judges"), judge.name),
            (reverse("view_judge", args=[judge.pk]), judge.name),
            (reverse("enter_judge"), "Create Judge"),
            (reverse("view_scratches", args=[judge.pk]), "Scratches"),
            (reverse("view_schools"), school.name),
            (reverse("view_school", args=[school.pk]), school.name),
            (reverse("enter_school"), "Create School"),
            (reverse("view_rooms"), room.name),
            (reverse("view_room", args=[room.pk]), room.name),
            (reverse("enter_room"), "Create Room"),
            (reverse("bulk_check_in"), "true"),
            (reverse("manage_room_tags"), "Room Tags"),
            (reverse("view_teams"), team.name),
            (reverse("view_team", args=[team.pk]), team.name),
            (reverse("enter_team"), "Create Team"),
            (reverse("view_scratches_team", args=[team.pk]), "Scratches"),
            (reverse("rank_teams_ajax"), "Team Rankings"),
            (reverse("rank_teams"), "Varsity Ranking"),
            (reverse("view_debaters"), debater.name),
            (reverse("view_debater", args=[debater.pk]), debater.name),
            (reverse("enter_debater"), "Create Debater"),
            (reverse("rank_debaters_ajax"), "Debater Rankings"),
            (reverse("rank_debaters"), "Varsity Ranking"),
            (reverse("view_status"), "Round Status for Round"),
            (reverse("view_rounds"), "Rounds"),
            (reverse("view_round", args=[round_obj.round_number]), "Round"),
            (reverse("add_scratch"), "Add Scratch"),
            (reverse("view_scratches"), "Scratches"),
            (reverse("settings_form"), "Settings"),
            (reverse("view_backups"), "Backups"),
            (reverse("upload_data"), "Upload Data"),
            (reverse("confirm_start_tourny"), "Are you sure?"),
            (reverse("break"), "This action will attempt to pair"),
            (reverse("pair_round"), "Pairing Round"),
            (reverse("re_pair_round"), "Re-pair"),
            ("/403/", "403"),
            ("/404/", "404"),
            ("/500/", "500"),
        ]

        if outround:
            views_to_test.extend([
                (reverse("outround_pairing_view", args=[0, 2]), "Outround Pairing"),
                (reverse("forum_view", args=[0]), "Forum Result Display"),
                (reverse("enter_result", args=[self.varsity_outround.pk]),
                 "Entering Ballot"),
            ])

        for url, expected_content in views_to_test:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200,
                f"Failed to render {url}, got status {response.status_code}")
            self.assertIn(expected_content, response.content.decode(),
                f"Expected content '{expected_content}' not found in {url}")

    def test_n_plus_one(self):
        views_to_test = [
            ("index",),
            ("view_judges",),
            ("view_schools",),
            ("view_rooms",),
            ("manage_room_tags",),
            ("view_teams",),
            ("rank_teams",),
            ("view_debaters",),
            ("rank_debaters",),
            ("view_status",),
            ("view_rounds",),
            ("view_scratches",),
            ("view_backups",),
            ("break",),
        ]

        for view_name_tuple in views_to_test:
            with profiler.Profiler():
                response = self.client.get(reverse(*view_name_tuple))
                self.assertEqual(response.status_code, 200,
                                 f"Failed to render {reverse(*view_name_tuple)}, "
                                 f" got status {response.status_code}")
