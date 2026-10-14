from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from mittab.apps.tab.auth_roles import PRESET_ADD_SCRATCHES, apply_staff_permission_preset
from mittab.apps.tab.models import (
    Bye, CheckIn, Judge, JudgeJudgeScratch, Room, RoomCheckIn, Round,
    TabSettings, Team, TeamTeamScratch,
)
from mittab.libs import assign_judges, tab_logic
from mittab.libs.cacheing import cache_logic


class TestExperimentalScratchDefaults(TestCase):
    fixtures = ["testing_db"]

    def setUp(self):
        TabSettings.set("cur_round", 1)
        CheckIn.objects.all().delete()
        RoomCheckIn.objects.all().delete()
        CheckIn.objects.bulk_create([
            CheckIn(judge=judge, round_number=1) for judge in Judge.objects.all()
        ])
        RoomCheckIn.objects.bulk_create([
            RoomCheckIn(room=room, round_number=1) for room in Room.objects.all()
        ])
        self.user = get_user_model().objects.create_superuser(
            "experimental", "experimental@example.com", "password",
        )
        self.client.force_login(self.user)

    def snapshot(self):
        return sorted(Round.objects.filter(round_number=1).values_list(
            "gov_team_id", "opp_team_id", "chair_id", "pullup",
        )), sorted(Bye.objects.values_list("bye_team_id", "round_number"))

    def test_disabled_stored_scratches_do_not_change_pairings_or_judges(self):
        tab_logic.pair_round()
        TabSettings.set("cur_round", 2)
        assign_judges.add_judges()
        baseline = self.snapshot()
        pairing = Round.objects.filter(round_number=1).first()
        TeamTeamScratch.objects.create(
            team_one=pairing.gov_team, team_two=pairing.opp_team,
        )
        judges = list(Judge.objects.all()[:2])
        JudgeJudgeScratch.objects.create(judge_one=judges[0], judge_two=judges[1])
        Round.objects.all().delete()
        Bye.objects.all().delete()
        cache_logic.clear_cache()
        TabSettings.set("cur_round", 1)
        TabSettings.set("enable_team_team_scratches", 0)
        TabSettings.set("enable_judge_judge_scratches", 0)
        with patch.object(tab_logic, "_pair_round_experimental") as team_path, \
                patch.object(assign_judges, "_add_judges_experimental") as judge_path, \
                patch.object(TeamTeamScratch.objects, "values_list") as team_query, \
                patch.object(JudgeJudgeScratch.objects, "values_list") as judge_query:
            tab_logic.pair_round()
            TabSettings.set("cur_round", 2)
            assign_judges.add_judges()
            self.assertEqual(self.snapshot(), baseline)
            team_path.assert_not_called()
            judge_path.assert_not_called()
            team_query.assert_not_called()
            judge_query.assert_not_called()

    def test_disabled_ui_preserves_existing_scratch_editor(self):
        response = self.client.get(reverse("add_scratch"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("forms_by_type", response.context)
        self.assertEqual(
    self.client.get(
        reverse("experimental_add_scratch")).status_code,
         404)
        team = Team.objects.first()
        response = self.client.get(reverse("view_team", args=[team.pk]))
        self.assertNotContains(response, "Experimental:")

    def test_enabling_one_type_does_not_enable_the_other(self):
        TabSettings.set("enable_team_team_scratches", 1)
        response = self.client.get(reverse("experimental_add_scratch"))
        self.assertContains(response, "Experimental")
        self.assertIn("team_team", response.context["forms_by_type"])
        self.assertNotIn("judge_judge", response.context["forms_by_type"])
        response = self.client.post(reverse("experimental_add_scratch"), {
            "form_type": "judge_judge",
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(JudgeJudgeScratch.objects.exists())

    def test_restricted_scratch_staff_cannot_access_experimental_editor(self):
        TabSettings.set("enable_team_team_scratches", 1)
        apply_staff_permission_preset(self.user, PRESET_ADD_SCRATCHES)
        self.user.is_superuser = False
        self.user.save()
        response = self.client.get(reverse("experimental_add_scratch"))
        self.assertIn(response.status_code, (302, 403))

    def test_failed_experimental_pairing_rolls_back_side_effects(self):
        from mittab.libs import errors  # pylint: disable=import-outside-toplevel
        TabSettings.set("enable_team_team_scratches", 1)
        TabSettings.set("pairing_released", 1)
        teams = list(Team.objects.filter(checked_in=True))
        for one_index, one in enumerate(teams):
            for two in teams[one_index + 1:]:
                TeamTeamScratch.objects.create(team_one=one, team_two=two)
        baseline = self.snapshot()
        with self.assertRaises(errors.NotEnoughTeamsError):
            tab_logic.pair_round()
        self.assertEqual(self.snapshot(), baseline)
        self.assertEqual(TabSettings.get("pairing_released"), 1)

    def test_admin_controls_are_unavailable_until_enabled(self):
        response = self.client.get(reverse("admin:index"))
        self.assertNotContains(response, "Judge judge scratches")
        response = self.client.get(reverse("admin:tab_judgejudgescratch_add"))
        self.assertEqual(response.status_code, 403)
        TabSettings.set("enable_judge_judge_scratches", 1)
        response = self.client.get(reverse("admin:tab_judgejudgescratch_add"))
        self.assertEqual(response.status_code, 200)


    def test_enabled_features_without_scratches_keep_standard_pairing_paths(self):
        TabSettings.set("enable_team_team_scratches", 1)
        TabSettings.set("enable_judge_judge_scratches", 1)
        TeamTeamScratch.objects.all().delete()
        JudgeJudgeScratch.objects.all().delete()
        with patch.object(tab_logic, "_pair_round_experimental") as team_path, \
                patch.object(assign_judges, "_add_judges_experimental") as judge_path:
            tab_logic.pair_round()
            TabSettings.set("cur_round", 2)
            assign_judges.add_judges()
            team_path.assert_not_called()
            judge_path.assert_not_called()
            self.assertTrue(Round.objects.filter(round_number=1).exists())
