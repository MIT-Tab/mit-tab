"""Re-pairing resets card annotations without losing historical evidence."""
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from mittab.apps.tab.board_audit import pairing_history
from mittab.apps.tab.judge_history import current_manual_judge_events
from mittab.apps.tab.models import (
    AuditEvent, BreakingTeam, CheckIn, Judge, ManualJudgeAssignment, Outround,
    Round, School, TabSettings, Team,
)
from mittab.libs import assign_judges, errors


@pytest.mark.django_db
class TestJudgeHistory(TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser(
            username="tab", password="password", email="tab@example.com")
        self.client.force_login(self.user)
        TabSettings.set("cur_round", 2)
        TabSettings.set("tot_rounds", 5)
        TabSettings.set("pair_wings", 0)
        TabSettings.set("var_panel_size", 1)
        TabSettings.set("nov_panel_size", 1)
        school = School.objects.create(name="History School")
        teams = [Team.objects.create(name=f"Team {i}", school=school, seed=0)
                 for i in range(4)]
        self.inround = Round.objects.create(
            round_number=1, gov_team=teams[0], opp_team=teams[1])
        self.other_inround = Round.objects.create(
            round_number=2, gov_team=teams[2], opp_team=teams[3])
        for i, team in enumerate(teams, 1):
            BreakingTeam.objects.create(
                team=team, seed=i, effective_seed=i, type_of_team=Outround.VARSITY)
        self.outround = Outround.objects.create(
            num_teams=4, type_of_round=Outround.VARSITY,
            gov_team=teams[0], opp_team=teams[3])
        self.decided_outround = Outround.objects.create(
            num_teams=4, type_of_round=Outround.VARSITY,
            gov_team=teams[1], opp_team=teams[2], victor=Outround.GOV)
        self.novice_outround = Outround.objects.create(
            num_teams=2, type_of_round=Outround.NOVICE,
            gov_team=teams[1], opp_team=teams[2])
        self.judge = Judge.objects.create(name="Judge", rank=5)
        for round_number in (0, 1):
            CheckIn.objects.create(judge=self.judge, round_number=round_number)

    def _manual_edits(self, pairing):
        pairing.judges.add(self.judge)
        pairing.chair = self.judge
        pairing.save()
        return [AuditEvent.record(
            pairing, event_type, self.user, changes={field: "Judge"})
                for event_type, field in (
                    (AuditEvent.MANUAL_JUDGE_ASSIGN, "assigned_judge"),
                    (AuditEvent.MANUAL_JUDGE_REMOVE, "removed_judge"),
                    (AuditEvent.MANUAL_CHAIR_CHANGE, "chair"))]

    def _current_events(self, pairing):
        return list(current_manual_judge_events(type(pairing), [pairing.pk]))

    def test_inround_repair_clears_card_and_preserves_audit_history(self):
        old_events = self._manual_edits(self.inround)
        other_events = self._manual_edits(self.other_inround)
        self.assertCountEqual(self._current_events(self.inround), old_events)
        before = self.client.get(reverse("view_status"))
        self.assertContains(before, "manual edits (3)")

        with patch("mittab.apps.tab.views.pairing_views.backup.backup_round"):
            response = self.client.post(reverse("assign_judges"))
        self.assertEqual(response.status_code, 302)

        self.assertEqual(self._current_events(self.inround), [])
        self.assertCountEqual(self._current_events(self.other_inround), other_events)
        self.assertEqual(len(pairing_history(Round, self.inround.pk)), 3)
        self.assertFalse(ManualJudgeAssignment.objects.filter(round=self.inround).exists())
        self.assertTrue(self.inround.judges.filter(pk=self.judge.pk).exists())
        after = self.client.get(reverse("view_status"))
        self.assertNotContains(after, "manual edits (")
        self.assertNotContains(after, "manual-lay")

        new_event = AuditEvent.record(
            self.inround, AuditEvent.MANUAL_CHAIR_CHANGE, self.user)
        self.assertEqual(self._current_events(self.inround), [new_event])
        self.assertContains(self.client.get(reverse("view_status")), "manual edits (1)")
        assign_judges.add_judges()
        self.assertEqual(self._current_events(self.inround), [])

    def test_outround_repair_only_resets_selected_undecided_cards(self):
        old_events = self._manual_edits(self.outround)
        decided_events = self._manual_edits(self.decided_outround)
        novice_events = self._manual_edits(self.novice_outround)
        url = reverse("outround_pairing_view", args=[Outround.VARSITY, 4])
        self.assertContains(self.client.get(url), "manual edits (3)", count=3)

        assign_judges.add_outround_judges(round_specs=[(Outround.VARSITY, 4)])

        self.assertEqual(self._current_events(self.outround), [])
        self.assertCountEqual(self._current_events(self.decided_outround), decided_events)
        self.assertCountEqual(self._current_events(self.novice_outround), novice_events)
        self.assertTrue(AuditEvent.objects.filter(pk__in=[e.pk for e in old_events]).exists())
        self.assertEqual(len(pairing_history(Outround, self.outround.pk)), 3)
        response = self.client.get(url)
        self.assertNotIn(self.outround.pk, response.context["manual_judge_audit_events"])
        self.assertContains(response, "manual edits (3)", count=2)

        new_event = AuditEvent.record(self.outround, AuditEvent.MANUAL_JUDGE_ASSIGN)
        self.assertEqual(self._current_events(self.outround), [new_event])

    def test_team_swap_and_side_highlights_persist_in_both_staff_views(self):
        for pairing, assign_route, side_route, page in (
                (self.inround, "assign_team", "switch_sides",
                 reverse("view_round", args=[self.inround.round_number])),
                (self.outround, "outround_assign_team", "outround_switch_sides",
                 reverse("outround_pairing_view", args=[Outround.VARSITY, 4]))):
            with self.subTest(model=type(pairing).__name__):
                replacement = self.other_inround.gov_team
                response = self.client.get(reverse(
                    assign_route, args=[pairing.pk, "gov", replacement.pk]))
                self.assertTrue(response.json()["success"])
                self.assertContains(self.client.get(page), 'gov-team manual-team')
                AuditEvent.record(pairing, AuditEvent.JUDGES_PAIRED)
                self.assertContains(self.client.get(page), 'gov-team manual-team')
                response = self.client.get(reverse(side_route, args=[pairing.pk]))
                self.assertTrue(response.json()["success"])
                after = self.client.get(page)
                self.assertContains(after, 'gov-team manual-team')
                self.assertContains(after, 'opp-team manual-team')

    def test_failed_repair_preserves_assignments_and_manual_history(self):
        for pairing, repair in (
                (self.inround, assign_judges.add_judges),
                (self.outround, assign_judges.add_outround_judges)):
            with self.subTest(model=type(pairing).__name__):
                events = self._manual_edits(pairing)
                with patch("mittab.libs.assign_judges.mwmatching.maxWeightMatching",
                           side_effect=errors.JudgeAssignmentError("Impossible pairing")):
                    with self.assertRaises(errors.JudgeAssignmentError):
                        repair()
                self.assertCountEqual(self._current_events(pairing), events)
                pairing.refresh_from_db()
                self.assertEqual(pairing.chair_id, self.judge.pk)
                self.assertEqual(list(pairing.judges.all()), [self.judge])
                if isinstance(pairing, Round):
                    self.assertTrue(ManualJudgeAssignment.objects.filter(round=pairing).exists())
