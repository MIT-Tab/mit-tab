import re
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from nplusone.core import profiler

from mittab.apps.tab.models import (
    BALLOT_CODE_MAX_LENGTH,
    Room,
    RoomCheckIn,
    TabSettings,
    Team,
    Round,
    RoundStats,
    Judge,
    School,
    Debater,
    Outround,
    ManualJudgeAssignment,
    JudgeCodeEmailLog,
    WrittenRFDEmailLog,
    Scratch,
    AuditEvent,
)
from mittab.libs.email_service import EmailServiceError
from mittab.apps.tab.views.judge_views import EMAIL_RATE_LIMIT_WINDOW


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
            (reverse("re_pair_round"), "re-pairing"),
            (reverse("round_stats"), "Tournament Overview")
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

    def test_staff_ballot_view_shows_written_rfd(self):
        round_obj = Round.objects.filter(round_number=1).first()
        round_obj.rfd = "Gov won because they controlled the weighing."
        round_obj.save(update_fields=["rfd"])
        TabSettings.set("written_rfd_first_round", 1)
        TabSettings.set("written_rfd_deadline", "2099-01-01 12:00")

        response = self.client.get(f"/round/{round_obj.id}/result/")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Ballot submitted", content)
        self.assertIn("Reason for Decision", content)
        self.assertIn("Save Written RFD", content)
        self.assertIn("Gov won because they controlled the weighing.", content)

        response = self.client.post(
            f"/round/{round_obj.id}/result/",
            {"rfd": "Staff updated the RFD text."},
        )

        self.assertEqual(response.status_code, 302)
        round_obj.refresh_from_db()
        self.assertEqual(round_obj.rfd, "Staff updated the RFD text.")

    def test_staff_unsubmitted_ballot_form_shows_written_rfd_field(self):
        round_obj = Round.objects.filter(round_number=1).first()
        RoundStats.objects.filter(round=round_obj).delete()
        round_obj.victor = Round.NONE
        round_obj.save(update_fields=["victor"])
        TabSettings.set("written_rfd_first_round", 1)
        TabSettings.set("written_rfd_deadline", "2099-01-01 12:00")

        response = self.client.get(f"/round/{round_obj.id}/result/")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Written RFD", content)
        self.assertIn("Reason for Decision", content)
        self.assertIn("Optional. You may submit the ballot without an RFD", content)

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
            ("round_stats",),
        ]

        for view_name_tuple in views_to_test:
            with profiler.Profiler():
                response = self.client.get(reverse(*view_name_tuple))
                self.assertEqual(response.status_code, 200,
                                 f"Failed to render {reverse(*view_name_tuple)}, "
                                 f" got status {response.status_code}")

    def test_manual_judge_indicator_visible(self):
        round_obj = Round.objects.filter(round_number=1).first()
        round_obj.judges.clear()
        judge = Judge.objects.first()
        round_obj.judges.add(judge)

        response = self.client.get(reverse("view_status"))

        card_html = self._round_card_html(response, round_obj.id)
        self.assertIn("manual-lay", card_html)
        self.assertTrue(
            ManualJudgeAssignment.objects.filter(round=round_obj, judge=judge).exists()
        )

    def test_manual_judge_indicator_removed_after_unassign(self):
        round_obj = Round.objects.filter(round_number=1).first()
        round_obj.judges.clear()
        judge = Judge.objects.first()
        round_obj.judges.add(judge)
        round_obj.judges.remove(judge)

        response = self.client.get(reverse("view_status"))

        card_html = self._round_card_html(response, round_obj.id)
        self.assertNotIn("manual-lay", card_html)
        self.assertFalse(
            ManualJudgeAssignment.objects.filter(round=round_obj, judge=judge).exists()
        )

    def test_team_creator_and_edit_audit_visible(self):
        school = School.objects.first()
        debaters = [
            Debater.objects.create(
                name="Audit Debater One",
                novice_status=Debater.VARSITY,
            ),
            Debater.objects.create(
                name="Audit Debater Two",
                novice_status=Debater.VARSITY,
            ),
        ]

        response = self.client.post(reverse("enter_team"), {
            "name": "Audit Team",
            "school": school.id,
            "debaters": [debater.id for debater in debaters],
            "seed": Team.UNSEEDED,
            "break_preference": Team.VARSITY,
            "checked_in": "on",
            "ranking_public": "on",
            "number_scratches": 0,
        })

        self.assertEqual(response.status_code, 302)
        team = Team.objects.get(name="Audit Team")
        self.assertEqual(team.created_by, self.user)
        self.assertTrue(
            AuditEvent.objects.filter(
                content_type__model="team",
                object_id=team.id,
                event_type=AuditEvent.CREATE,
                user=self.user,
            ).exists()
        )

        response = self.client.post(reverse("view_team", args=[team.id]), {
            "name": "Audit Team Renamed",
            "school": school.id,
            "debaters": [debater.id for debater in debaters],
            "seed": Team.UNSEEDED,
            "break_preference": Team.VARSITY,
            "checked_in": "on",
            "ranking_public": "on",
        })

        self.assertEqual(response.status_code, 302)
        edit_event = AuditEvent.objects.get(
            content_type__model="team",
            object_id=team.id,
            event_type=AuditEvent.EDIT,
            user=self.user,
        )
        self.assertIn("name", edit_event.changes["fields"])

        response = self.client.get(reverse("view_team", args=[team.id]))
        content = response.content.decode()
        self.assertIn("Audit Trail", content)
        self.assertIn("testuser", content)
        self.assertIn("Edited", content)

    def test_scratch_creator_and_edit_audit_visible(self):
        school = School.objects.first()
        debaters = [
            Debater.objects.create(
                name="Scratch Audit Debater One",
                novice_status=Debater.VARSITY,
            ),
            Debater.objects.create(
                name="Scratch Audit Debater Two",
                novice_status=Debater.VARSITY,
            ),
        ]
        team = Team.objects.create(
            name="Scratch Audit Team",
            school=school,
            seed=Team.UNSEEDED,
            break_preference=Team.VARSITY,
        )
        team.debaters.add(*debaters)
        judge = Judge.objects.first()

        response = self.client.post(reverse("add_scratch"), {
            "team": team.id,
            "judge": judge.id,
            "scratch_type": Scratch.TEAM_SCRATCH,
        })

        self.assertEqual(response.status_code, 302)
        scratch = Scratch.objects.get(team=team, judge=judge)
        self.assertEqual(scratch.created_by, self.user)

        response = self.client.post(
            reverse("view_scratches_team", args=[team.id]),
            {
                "1-team": team.id,
                "1-judge": judge.id,
                "1-scratch_type": Scratch.TAB_SCRATCH,
            },
        )

        self.assertEqual(response.status_code, 302)
        edit_event = AuditEvent.objects.get(
            content_type__model="scratch",
            object_id=scratch.id,
            event_type=AuditEvent.EDIT,
            user=self.user,
        )
        self.assertIn("scratch_type", edit_event.changes["fields"])

        response = self.client.get(reverse("view_scratches_team", args=[team.id]))
        content = response.content.decode()
        self.assertIn("Created by testuser", content)
        self.assertIn("Edited by testuser", content)

    def test_manual_judge_assignment_tracks_actor(self):
        round_obj = Round.objects.filter(round_number=1).first()
        round_obj.judges.clear()
        judge = Judge.objects.first()

        response = self.client.get(
            reverse("assign_judge", args=[round_obj.id, judge.id])
        )

        self.assertEqual(response.status_code, 200)
        assignment = ManualJudgeAssignment.objects.get(round=round_obj, judge=judge)
        self.assertEqual(assignment.created_by, self.user)
        self.assertTrue(
            AuditEvent.objects.filter(
                content_type__model="round",
                object_id=round_obj.id,
                event_type=AuditEvent.MANUAL_JUDGE_ASSIGN,
                user=self.user,
            ).exists()
        )

        response = self.client.get(reverse("view_status"))
        self.assertIn("Manually assigned by testuser", response.content.decode())

    def test_view_room_saves_outround_checkin(self):
        room = Room.objects.first()
        url = reverse("view_room", args=[room.pk])
        post_data = {
            "name": room.name,
            "rank": room.rank,
            "checkin_-1": "on",
        }

        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            RoomCheckIn.objects.filter(room=room, round_number=0).exists()
        )

        post_data.pop("checkin_-1")
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            RoomCheckIn.objects.filter(room=room, round_number=0).exists()
        )

    def _round_card_html(self, response, round_id):
        content = response.content.decode()
        match = re.search(
            rf'<div class="row" round-id="{round_id}".*?</div>\s*</div>',
            content,
            re.DOTALL,
        )
        self.assertIsNotNone(match, f"Round card for round {round_id} not found")
        return match.group(0)

    @mock.patch("mittab.apps.tab.views.judge_views.EmailService")
    def test_send_judge_codes_view(self, email_service):
        judge = Judge.objects.first()
        judge.email = "judge@example.com"
        judge.save()

        email_service.return_value.send_bulk.return_value = 1

        response = self.client.get(reverse("send_judge_codes"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("judge@example.com", response.content.decode())

        response = self.client.post(
            reverse("send_judge_codes"),
            {"judge_ids": [str(judge.id)]},
        )
        self.assertEqual(response.status_code, 302)
        email_service.return_value.send_bulk.assert_called_once()

    def test_send_judge_codes_defaults_to_never_received(self):
        never_sent_judge = Judge.objects.first()
        never_sent_judge.email = "judge@example.com"
        never_sent_judge.save()

        previously_sent_judge = Judge.objects.create(
            name="Previously Sent Judge",
            rank=3.5,
            email="prior@example.com",
        )
        previously_sent_judge.schools.add(School.objects.first())

        JudgeCodeEmailLog.objects.create(
            judge=previously_sent_judge,
            email=previously_sent_judge.email,
            ballot_code=previously_sent_judge.ballot_code,
        )
        JudgeCodeEmailLog.objects.filter(judge=previously_sent_judge).update(
            sent_at=timezone.now() - EMAIL_RATE_LIMIT_WINDOW - timedelta(minutes=1)
        )

        response = self.client.get(reverse("send_judge_codes"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        self.assertIn("Select Never Received", content)
        self.assertIn("Select All", content)
        self.assertRegex(
            content,
            rf'name="judge_ids" value="{never_sent_judge.id}" checked',
        )
        self.assertRegex(
            content,
            rf'name="judge_ids" value="{previously_sent_judge.id}" >',
        )

    @mock.patch("mittab.apps.tab.views.judge_views.EmailService")
    def test_send_judge_codes_deduplicates_emails(self, email_service):
        email_service.return_value.send_bulk.return_value = 1

        judge = Judge.objects.first()
        judge.email = "judge@example.com"
        judge.save()

        other = Judge.objects.create(
            name="Extra Judge",
            rank=3.5,
        )
        other.schools.add(School.objects.first())
        other.email = "judge@example.com"
        other.save()

        response = self.client.post(
            reverse("send_judge_codes"),
            {"judge_ids": [str(judge.id), str(other.id)]},
        )
        self.assertEqual(response.status_code, 302)
        # Only one email should be queued for the shared address
        email_service.return_value.send_bulk.assert_called_once()
        args, _kwargs = email_service.return_value.send_bulk.call_args
        self.assertEqual(len(list(args[0])), 1)

    @mock.patch("mittab.apps.tab.views.judge_views.EmailService")
    def test_send_judge_codes_rate_limited(self, email_service):
        judge = Judge.objects.first()
        judge.email = "judge@example.com"
        judge.save()

        JudgeCodeEmailLog.objects.create(
            judge=judge,
            email=judge.email,
            ballot_code=judge.ballot_code,
            sent_at=timezone.now()
        )

        response = self.client.post(
            reverse("send_judge_codes"),
            {"judge_ids": [str(judge.id)]},
        )
        self.assertEqual(response.status_code, 302)
        email_service.return_value.send_bulk.assert_not_called()

    @mock.patch("mittab.apps.tab.views.judge_views.EmailService")
    def test_send_judge_codes_logs_partial_successes(self, email_service):
        judge = Judge.objects.first()
        judge.email = "judge1@example.com"
        judge.save()

        other = Judge.objects.create(
            name="Extra Partial Judge",
            rank=3.5,
            email="judge2@example.com",
        )
        other.schools.add(School.objects.first())

        def fail_after_first(requests):
            raise EmailServiceError("boom", sent_requests=list(requests[:1]))

        email_service.return_value.send_bulk.side_effect = fail_after_first

        response = self.client.post(
            reverse("send_judge_codes"),
            {"judge_ids": [str(judge.id), str(other.id)]},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(JudgeCodeEmailLog.objects.count(), 1)
        self.assertEqual(JudgeCodeEmailLog.objects.first().judge, judge)

    @mock.patch("mittab.apps.tab.views.judge_views.EmailService")
    def test_send_written_rfds_view(self, email_service):
        round_obj = Round.objects.filter(round_number=1).first()
        round_obj.victor = Round.GOV
        round_obj.rfd = "Gov won the link debate."
        round_obj.save()
        TabSettings.set("written_rfd_first_round", 1)

        debaters = list(round_obj.gov_team.debaters.all()) + list(
            round_obj.opp_team.debaters.all()
        )
        for index, debater in enumerate(debaters):
            debater.email = f"debater{index}@example.com"
            debater.save()

        email_service.return_value.send_bulk.return_value = len(debaters)

        response = self.client.get(reverse("send_written_rfds"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Send Written RFDs", response.content.decode())
        self.assertIn(f"Round {round_obj.round_number}", response.content.decode())

        response = self.client.post(
            reverse("send_written_rfds"),
            {"round_ids": [str(round_obj.id)]},
        )

        self.assertEqual(response.status_code, 302)
        email_service.return_value.send_bulk.assert_called_once()
        sent_requests = list(email_service.return_value.send_bulk.call_args.args[0])
        self.assertIn(f"Judge: {round_obj.chair.name}", sent_requests[0].text_body)
        self.assertEqual(
            WrittenRFDEmailLog.objects.filter(round=round_obj).count(),
            len(debaters),
        )

    def test_judge_ballot_code_validation(self):
        judge = Judge.objects.first()
        judge.ballot_code = "alpha-bravo"
        self.assertTrue(judge.is_valid_ballot_code())

        judge.ballot_code = "TEST123"
        self.assertTrue(judge.is_valid_ballot_code())

        judge.ballot_code = "alpha-123"
        with self.assertRaises(ValidationError):
            judge.is_valid_ballot_code()

        judge.ballot_code = "a" * (BALLOT_CODE_MAX_LENGTH + 1)
        with self.assertRaises(ValidationError):
            judge.is_valid_ballot_code()
