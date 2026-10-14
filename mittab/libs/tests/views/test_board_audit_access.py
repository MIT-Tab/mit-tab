"""Board confidentiality and write boundaries are release invariants."""
from contextlib import ExitStack
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.tab.auth_roles import (
    APDA_BOARD_GROUP_NAME, board_can_access_view, is_apda_board_access_open,
)
from mittab.apps.tab.admin import RoundStatsAdmin
from mittab.apps.tab.board_audit import (
    pairing_history, pairing_revision, pairing_snapshot,
)
from mittab.apps.tab.board_operations import edit_board_outround, pair_board_outround
from mittab.apps.tab.forms import OutroundResultEntryForm
from mittab.apps.tab.models import (
    AuditEvent,
    BreakingTeam,
    Judge,
    Outround,
    Room,
    RoomCheckIn,
    Round,
    RoundStats,
    Scratch,
    TabSettings,
    Team,
)


@pytest.mark.django_db(transaction=True)
class TestBoardAuditAccess(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "board_auditor", password="password")
        group, _ = Group.objects.get_or_create(name=APDA_BOARD_GROUP_NAME)
        self.user.groups.add(group)
        self.client.force_login(self.user)
        self.teams = list(Team.objects.all()[:4])
        self.judge = Judge.objects.first()
        self.room = Room.objects.first()
        Outround.objects.all().delete()
        BreakingTeam.objects.all().delete()
        for seed, team in enumerate(self.teams, 1):
            BreakingTeam.objects.create(
                team=team, seed=seed, effective_seed=seed, type_of_team=0)
        self.pairing = Outround.objects.create(
            gov_team=self.teams[0], opp_team=self.teams[3], room=self.room,
            num_teams=4, type_of_round=0, victor=Outround.GOV, chair=self.judge,
        )
        self.pairing.judges.add(self.judge)

    def _edit(self, action, **data):
        self.pairing.refresh_from_db()
        return self.client.post(
            reverse("board_outround_edit", args=[self.pairing.pk]), {
                "action": action, "revision": pairing_revision(self.pairing), **data,
            })

    def _protected_state(self):
        return {
            "judges": list(Judge.objects.order_by("pk").values()),
            "scratches": list(Scratch.objects.order_by("pk").values()),
            "panels": list(Outround.judges.through.objects.order_by("pk").values()),
            "chairs": list(Outround.objects.order_by("pk").values_list(
                "pk", "chair_id")),
            "inrounds": list(Round.objects.order_by("pk").values()),
        }

    def _judge_logic_forbidden(self):
        stack = ExitStack()
        for name in (
            "add_judges",
            "add_outround_judges",
            "judge_conflict",
                "can_judge_teams"):
            stack.enter_context(
                patch(
                    f"mittab.libs.assign_judges.{name}",
                    side_effect=AssertionError("Board called judge logic")))
        stack.enter_context(
            patch(
                "mittab.libs.assign_rooms.add_outround_rooms",
                side_effect=AssertionError("Board called staff room logic")))
        return stack

    def test_timing_threshold_and_outround_milestone(self):
        Outround.objects.all().delete()
        number = int(TabSettings.get("tot_rounds"))
        final_ids = list(Round.objects.filter(
            round_number=number).values_list("pk", flat=True))[:5]
        Round.objects.filter(round_number=number).exclude(pk__in=final_ids).delete()
        self.assertEqual(len(final_ids), 5)
        Round.objects.filter(pk__in=final_ids).update(victor=Round.UNKNOWN)
        self.assertFalse(is_apda_board_access_open())
        Round.objects.filter(pk__in=final_ids[:3]).update(victor=Round.GOV)
        self.assertFalse(is_apda_board_access_open())
        Round.objects.filter(pk=final_ids[3]).update(victor=Round.GOV)
        self.assertTrue(is_apda_board_access_open())
        Round.objects.filter(pk__in=final_ids).update(victor=Round.UNKNOWN)
        Outround.objects.create(
            gov_team=self.teams[0], opp_team=self.teams[1], num_teams=2)
        self.assertTrue(is_apda_board_access_open())

    def test_closed_gate_blocks_existing_session_without_public_redirect(self):
        Outround.objects.all().delete()
        Round.objects.all().update(victor=Round.UNKNOWN)
        for name, args in (("apda_board_home", []), ("public_home", []),
                           ("board_inrounds", []), ("board_outrounds", []),
                           ("forum_post", []), ("download_black_rod_bundle", []),
                           ("tab_card", [self.teams[0].pk])):
            for method in ("get", "post", "head"):
                response = getattr(self.client, method)(reverse(name, args=args))
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get("Cache-Control"), "private, no-store")
                self.assertNotContains(response, self.teams[0].name, status_code=403)
        self.client.logout()
        self.assertFalse(self.client.login(
            username="board_auditor", password="password"))

    def test_protected_routes_are_blocked_even_with_staff_and_superuser_permissions(
            self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        urls = [
            "/admin/", "/admin/tab/judge/", "/admin/tab/scratch/", "/json",
            "/backup/download/example/", "/backup/restore/example/", "/backup/example/",
            "/upload_backup/", "/archive/download/", "/silk/",
            reverse("view_judge", args=[self.judge.pk]),
            reverse("team_portal", args=[self.teams[0].team_code]),
            reverse("judge_portal", args=[self.judge.ballot_code]),
            reverse("enter_result", args=[Round.objects.first().pk]),
            reverse("alternative_judges", args=[Round.objects.first().pk]),
            reverse("outround_alternative_judges", args=[self.pairing.pk]),
            reverse("assign_room_outround", args=[self.pairing.pk, self.room.pk]),
            reverse("outround_assign_judges"), reverse("view_backups"),
            reverse("manual_backup"), reverse("settings_form"),
        ]
        for url in urls:
            for method in ("get", "post", "head", "put", "delete"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.url, "/403/")
        self.assertEqual(self.client.get(reverse("board_outrounds")).status_code, 200)

    def test_new_routes_and_unlisted_methods_fail_closed(self):
        for name in (
            "board_future_export",
            "admin:tab_judge_changelist",
                "new_public_route"):
            for method in ("GET", "POST", "HEAD", "PUT", "DELETE"):
                self.assertFalse(board_can_access_view(name, method))
        self.assertFalse(board_can_access_view("board_inrounds", "POST"))
        self.assertFalse(board_can_access_view("board_outround_edit", "GET"))

    def test_safe_pages_do_not_query_scratches_or_judge_calibration(self):
        def guard(execute, sql, params, many, context):
            normalized = sql.lower().replace('`', '"')
            self.assertNotIn('tab_scratch', normalized)
            self.assertNotIn('"tab_judge"."rank"', normalized)
            self.assertNotIn('"tab_judge"."wing_only"', normalized)
            self.assertNotIn('"tab_judge"."ballot_code"', normalized)
            return execute(sql, params, many, context)
        urls = [
            reverse("board_inrounds"),
            reverse("board_outrounds"),
            reverse("board_judges"),
            reverse("board_tab_cards"),
            reverse(
                "board_tab_card",
                args=[
                    self.teams[0].pk])]
        with connection.execute_wrapper(guard):
            for url in urls:
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_changing_secrets_cannot_change_board_read_data(self):
        urls = [
            reverse("board_inrounds"),
            reverse("board_outrounds"),
            reverse("board_judges"),
            reverse(
                "board_tab_card",
                args=[
                    self.teams[0].pk]),
            reverse("forum_post"),
            reverse("download_black_rod_bundle")]
        # Compare structured data for dynamic pages, ignoring CSRF/export timestamps.
        before = [self.client.get(url) for url in urls]
        Scratch.objects.all().delete()
        Scratch.objects.create(
            team=self.teams[0], judge=self.judge, scratch_type=Scratch.TEAM_SCRATCH)
        Judge.objects.all().update(rank="9.99", wing_only=True)
        after = [self.client.get(url) for url in urls]
        for left, right in zip(before[:4], after[:4]):
            for key in ("pairings", "divisions", "judges", "team"):
                if key in left.context:
                    self.assertEqual(left.context[key], right.context[key])
        self.assertEqual(before[4].content, after[4].content)
        first = json.loads(before[5].content)
        second = json.loads(after[5].content)
        first.pop("exported_at")
        second.pop("exported_at")
        self.assertEqual(first, second)

    def test_audit_projection_excludes_protected_keys_notes_and_events(self):
        snapshot = pairing_snapshot(self.pairing)
        snapshot["rank"] = "CALIBRATION_SECRET"
        snapshot["judges"][0]["rank"] = "CALIBRATION_SECRET"
        AuditEvent.record(self.pairing, AuditEvent.EDIT, self.user, changes={
            "public": {"before": None, "after": snapshot},
            "scratch": "SCRATCH_SECRET",
        }, note="SCRATCH_SECRET CALIBRATION_SECRET")
        AuditEvent.record(self.pairing, AuditEvent.EDIT, self.user,
                          changes={"rank": "CALIBRATION_SECRET"}, note="SCRATCH_SECRET")
        response = self.client.get(reverse("board_outrounds"))
        self.assertNotContains(response, "SCRATCH_SECRET")
        self.assertNotContains(response, "CALIBRATION_SECRET")
        self.assertEqual(len(pairing_history(Outround, self.pairing.pk)), 1)

    def test_malformed_legacy_audit_values_cannot_smuggle_protected_data(self):
        AuditEvent.record(self.pairing, AuditEvent.MANUAL_JUDGE_ASSIGN, self.user,
                          changes={"assigned_judge": {"rank": "CALIBRATION_SECRET"}})
        snapshot = pairing_snapshot(self.pairing)
        snapshot["judges"][0]["name"] = {"scratch": "SCRATCH_SECRET"}
        AuditEvent.record(self.pairing, AuditEvent.EDIT, self.user, changes={
            "public": {"before": None, "after": snapshot},
        })
        response = self.client.get(reverse("board_outrounds"))
        self.assertNotContains(response, "CALIBRATION_SECRET")
        self.assertNotContains(response, "SCRATCH_SECRET")

    def test_only_documented_fields_can_reach_pairing_context(self):
        response = self.client.get(reverse("board_outrounds"))
        row = response.context["divisions"][0]["stages"][0]["pairings"][0]
        self.assertEqual(set(row), {
            "id", "revision", "gov_team", "opp_team", "room", "judges", "chair",
            "victor", "outcome", "num_teams", "division", "choice", "sidelock",
            "history",
        })
        for judge in row["judges"]:
            self.assertEqual(set(judge), {"id", "name"})
        self.assertEqual(set(row["gov_team"]), {"id", "name"})

    def test_board_inrounds_are_read_only_with_no_dropdowns_or_result_edits(self):
        response = self.client.get(reverse("board_inrounds"))
        for marker in (
            "judge-toggle",
            "team-toggle",
            "room-toggle",
            "tabcard",
            "Edit in Admin",
            "alternative_judges",
            "Enter Ballot",
                "Wing Only"):
            self.assertNotContains(response, marker)
        before = self._protected_state()
        self.client.post(reverse("board_inrounds"), {"victor": 2})
        self.assertEqual(self._protected_state(), before)

    def test_result_side_team_room_and_choice_changes_never_touch_protected_state(self):
        protected = self._protected_state()
        with self._judge_logic_forbidden():
            self._edit("result", victor=2, rank="1.00",
                       judges="999", round_instance="999")
            self._edit("sides")
            self._edit("team", position="gov", team=self.teams[2].pk)
            self._edit("room", room=Room.objects.last().pk)
            self._edit("choice", choice=2)
        self.assertEqual(self._protected_state(), protected)
        self.pairing.refresh_from_db()
        self.assertEqual(self.pairing.victor, Outround.UNKNOWN)
        self.assertEqual(self.pairing.choice, Outround.OPP)
        self.assertGreaterEqual(len(pairing_history(Outround, self.pairing.pk)), 5)

    def test_services_also_reject_actors_without_board_membership(self):
        user = get_user_model().objects.create_user("ordinary_user")
        before = self._protected_state()
        with self.assertRaises(ValidationError):
            pair_board_outround(0, user)
        with self.assertRaises(ValidationError):
            edit_board_outround(self.pairing.pk, "result", {
                "victor": 2, "revision": pairing_revision(self.pairing),
            }, user)
        self.assertEqual(self._protected_state(), before)

    def test_side_swap_preserves_winner_and_forfeit(self):
        self.pairing.victor = Outround.GOV_VIA_FORFEIT
        self.pairing.choice = Outround.GOV
        self.pairing.save()
        winner_id = self.pairing.winner.pk
        self._edit("sides")
        self.pairing.refresh_from_db()
        self.assertEqual(self.pairing.winner.pk, winner_id)
        self.assertEqual(self.pairing.victor, Outround.OPP_VIA_FORFEIT)
        self.assertEqual(self.pairing.choice, Outround.OPP)

    def test_swapping_teams_between_matches_clears_results_but_preserves_panels(self):
        peer = Outround.objects.create(
            gov_team=self.teams[1],
            opp_team=self.teams[2],
            num_teams=4,
            type_of_round=0,
            victor=Outround.OPP,
            chair=self.judge)
        peer.judges.add(self.judge)
        protected = self._protected_state()
        self._edit("team", position="gov", team=self.teams[1].pk)
        self.pairing.refresh_from_db()
        peer.refresh_from_db()
        self.assertEqual(self.pairing.gov_team_id, self.teams[1].pk)
        self.assertEqual(peer.gov_team_id, self.teams[0].pk)
        self.assertEqual(peer.victor, Outround.UNKNOWN)
        self.assertEqual(self.pairing.victor, Outround.UNKNOWN)
        self.assertEqual(protected, self._protected_state())

    def test_cross_division_invalid_choice_and_stale_revision_are_rejected(self):
        foreign = Team.objects.exclude(pk__in=[team.pk for team in self.teams]).first()
        BreakingTeam.objects.create(
            team=foreign, seed=1, effective_seed=1, type_of_team=1)
        before = pairing_snapshot(self.pairing)
        self._edit("team", position="gov", team=foreign.pk)
        self._edit("result", victor=99)
        self.client.post(reverse("board_outround_edit", args=[self.pairing.pk]), {
            "action": "result", "victor": 2, "revision": "outdated",
        })
        self.pairing.refresh_from_db()
        self.assertEqual(pairing_snapshot(self.pairing), before)
        self.assertEqual(pairing_history(Outround, self.pairing.pk), [])

    def test_dino_edit_can_only_change_is_dino(self):
        before = list(Judge.objects.order_by("pk").values())
        self.client.post(reverse("board_judges"), {
            "judge": self.judge.pk, "is_dino": "on", "rank": "1.00",
            "wing_only": "on", "ballot_code": "stolen-code",
        })
        after = list(Judge.objects.order_by("pk").values())
        for old, new in zip(before, after):
            if old["id"] == self.judge.pk:
                self.assertTrue(new["is_dino"])
                old["is_dino"] = True
            self.assertEqual(old, new)

    def test_pairing_teams_and_rooms_has_no_judge_logic_and_preserves_existing_panels(
            self):
        self.pairing.victor = Outround.OPP
        self.pairing.save()
        peer = Outround.objects.create(
            gov_team=self.teams[1],
            opp_team=self.teams[2],
            num_teams=4,
            type_of_round=0,
            victor=Outround.GOV)
        RoomCheckIn.objects.get_or_create(room=self.room, round_number=0)
        protected = self._protected_state()
        with self._judge_logic_forbidden():
            size = pair_board_outround(0, self.user)
        self.assertEqual(size, 2)
        final = Outround.objects.get(num_teams=2, type_of_round=0)
        self.assertEqual({final.gov_team_id, final.opp_team_id},
                         {self.teams[3].pk, peer.gov_team_id})
        self.assertIsNotNone(final.room_id)
        self.assertIsNone(final.chair_id)
        self.assertEqual(final.judges.count(), 0)
        after = self._protected_state()
        after["chairs"] = [item for item in after["chairs"] if item[0] != final.pk]
        self.assertEqual(protected, after)

    def test_incomplete_and_stale_brackets_cannot_advance(self):
        self.pairing.victor = Outround.UNKNOWN
        self.pairing.save()
        with self.assertRaises(ValidationError):
            pair_board_outround(0, self.user)
        self.pairing.victor = Outround.GOV
        self.pairing.save()
        Outround.objects.create(
            gov_team=self.teams[3],
            opp_team=self.teams[1],
            num_teams=2,
            type_of_round=0,
            victor=Outround.GOV)
        with self.assertRaises(ValidationError):
            pair_board_outround(0, self.user)
        self.assertContains(self.client.get(
            reverse("board_outrounds")), "previously eliminated")

    def test_reversing_upset_restores_effective_seeds(self):
        self._edit("result", victor=Outround.OPP)
        self.assertEqual(BreakingTeam.objects.get(team=self.teams[3]).effective_seed, 1)
        self._edit("result", victor=Outround.GOV)
        self.assertEqual(BreakingTeam.objects.get(team=self.teams[3]).effective_seed, 4)

    def test_failures_roll_back_pairings_audit_and_seed_changes(self):
        before = pairing_snapshot(self.pairing)
        with patch("mittab.apps.tab.board_operations.recompute_seeds",
                   side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                edit_board_outround(self.pairing.pk, "result", {
                    "victor": 2, "revision": pairing_revision(self.pairing),
                }, self.user)
        self.pairing.refresh_from_db()
        self.assertEqual(pairing_snapshot(self.pairing), before)
        self.assertEqual(pairing_history(Outround, self.pairing.pk), [])

    def test_csrf_is_required_for_board_corrections(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(
            client.post(
                reverse(
                    "board_outround_edit", args=[
                        self.pairing.pk]), {
                    "action": "result", "victor": 2, "revision": pairing_revision(
                        self.pairing), }).status_code, 403)

    def test_staff_result_form_cannot_save_a_different_hidden_target(self):
        peer = Outround.objects.create(
            gov_team=self.teams[1], opp_team=self.teams[2], num_teams=4)
        form = OutroundResultEntryForm({"winner": "2", "round_instance": peer.pk},
                                       round_instance=self.pairing)
        self.assertTrue(form.is_valid())
        with self.assertRaises(ValueError):
            form.save()
        peer.refresh_from_db()
        self.assertEqual(peer.victor, Outround.UNKNOWN)

    def test_initial_break_pairs_teams_and_rooms_without_judge_assignment(self):
        Outround.objects.all().delete()
        BreakingTeam.objects.all().delete()
        TabSettings.set("var_teams_to_break", 4)
        TabSettings.set("nov_teams_to_break", 0)
        for room in Room.objects.all():
            RoomCheckIn.objects.get_or_create(room=room, round_number=0)
        with self._judge_logic_forbidden():
            self.assertEqual(pair_board_outround(0, self.user), 4)
        rounds = list(Outround.objects.all())
        self.assertEqual(len(rounds), 2)
        for pairing in rounds:
            self.assertIsNotNone(pairing.room_id)
            self.assertIsNone(pairing.chair_id)
            self.assertFalse(pairing.judges.exists())
            self.assertEqual(len(pairing_history(Outround, pairing.pk)), 1)

    def test_insufficient_rooms_cannot_create_partial_initial_break(self):
        Outround.objects.all().delete()
        BreakingTeam.objects.all().delete()
        RoomCheckIn.objects.filter(round_number=0).delete()
        with self.assertRaises(ValidationError):
            pair_board_outround(0, self.user)
        self.assertFalse(Outround.objects.exists())
        self.assertFalse(BreakingTeam.objects.exists())

    def test_admin_score_edits_are_visible_in_read_only_inround_history(self):
        score = RoundStats.objects.first()
        pairing = score.round
        before = score.speaks
        score.speaks += 1
        RoundStatsAdmin(RoundStats, admin.site).save_model(
            SimpleNamespace(user=self.user), score, SimpleNamespace(), True)
        history = pairing_history(Round, pairing.pk)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["source"], "admin scores")
        self.assertIn(str(before), str(history))
        self.assertIn(str(score.speaks), str(history))

    def test_removed_pairings_remain_auditable_without_raw_notes(self):
        pairing = Round.objects.first()
        object_id, number = pairing.pk, pairing.round_number
        AuditEvent.record(pairing, AuditEvent.EDIT, self.user, changes={
            "public": {"before": pairing_snapshot(pairing), "after": None,
                       "source": "staff re-pairing"},
        }, note="SCRATCH_SECRET")
        pairing.delete()
        response = self.client.get(reverse("board_inrounds", args=[number]))
        self.assertContains(response, "Removed pairings")
        self.assertNotContains(response, "SCRATCH_SECRET")
        self.assertIn(object_id, [row["id"] for row in response.context["deleted"]])

    def test_legacy_tab_card_routes_lead_to_safe_board_cards(self):
        response = self.client.get(reverse("all_tab_cards"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "apda_board/tab_cards.html")
        response = self.client.get(reverse("pretty_tab_card", args=[self.teams[0].pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Team totals")
        response = self.client.get(reverse("tab_card", args=[self.teams[0].pk]))
        self.assertContains(response, "Team totals")
