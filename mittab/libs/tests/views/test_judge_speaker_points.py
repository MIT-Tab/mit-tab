from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from mittab.apps.tab.auth_roles import APDA_BOARD_GROUP_NAME
from mittab.apps.tab.models import (
    Debater, Judge, Round, RoundStats, School, TabSettings, Team,
)


class TestJudgeSpeakerPoints(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("director")
        school = School.objects.create(name="Test University")
        cls.gov = Team.objects.create(
            name="Gov team", school=school, seed=Team.UNSEEDED,
        )
        cls.opp = Team.objects.create(
            name="Opp team", school=school, seed=Team.UNSEEDED,
        )
        cls.debater = Debater.objects.create(
            name="Ironman", novice_status=Debater.VARSITY,
        )
        cls.single = Debater.objects.create(
            name="One round", novice_status=Debater.VARSITY,
        )
        cls.chair = Judge.objects.create(name="Chair", rank=5)
        cls.wing = Judge.objects.create(name="Wing", rank=5)
        cls.other = Judge.objects.create(name="Other", rank=5)

    def _round(self, number, victor, judges, scores):
        round_obj = Round.objects.create(
            round_number=number, gov_team=self.gov, opp_team=self.opp, victor=victor,
        )
        round_obj.judges.add(*judges)
        RoundStats.objects.bulk_create([
            RoundStats(round=round_obj, debater=debater, speaks=speaks, ranks=1)
            for debater, speaks in scores
        ])

    def test_panel_metrics_use_other_rounds_and_exclude_synthetic_scores(self):
        panel = [self.chair, self.wing]
        self._round(1, Round.GOV, panel, [(self.debater, 24), (self.debater, 30)])
        self._round(2, Round.OPP, [self.other], [(self.debater, 21), (self.single, 31)])
        self._round(3, Round.GOV, panel, [])
        self._round(4, Round.GOV_VIA_FORFEIT, panel, [(self.debater, 0)])
        self._round(5, Round.ALL_WIN, panel, [(self.debater, 50)])
        self._round(6, Round.UNKNOWN, panel, [(self.debater, 50)])
        self.client.force_login(self.admin)

        url = reverse("judge_speaker_points")
        default = self.client.get(url)
        self.assertContains(default, "No judges have 3 scored rounds yet")
        response = self.client.get(url, {"minimum_rounds": 1, "judge": self.wing.pk})
        self.assertEqual(response.context["metrics"], [
            {"id": self.chair.pk, "name": "Chair", "rounds": 1,
             "mean": Decimal("27.00"),
             "generosity": Decimal("6.00"), "mean_rank": 1, "generosity_rank": 1,
             "highlight": False},
            {"id": self.wing.pk, "name": "Wing", "rounds": 1,
             "mean": Decimal("27.00"),
             "generosity": Decimal("6.00"), "mean_rank": 1, "generosity_rank": 1,
             "highlight": True},
            {"id": self.other.pk, "name": "Other", "rounds": 1,
             "mean": Decimal("26.00"),
             "generosity": Decimal("-6.00"), "mean_rank": 3, "generosity_rank": 3,
             "highlight": False},
        ])
        self.assertContains(
            response, 'class="speaker-points-dot speaker-points-highlight"',
        )
        self.assertContains(response, f'value="{self.wing.pk}" selected')
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_judge_with_no_comparison_scores_still_has_mean(self):
        self._round(1, Round.GOV, [self.chair], [(self.debater, 24)])
        self.client.force_login(self.admin)
        url = reverse("judge_speaker_points")
        response = self.client.get(url, {"minimum_rounds": 1})
        self.assertIsNone(response.context["metrics"][0]["generosity"])
        self.assertIsNone(response.context["plot"])
        self.assertContains(response, "No scores in other rounds")

    def test_report_requires_admin_and_rejects_board_even_with_admin_role(self):
        self._round(1, Round.GOV, [self.chair], [(self.debater, 24)])
        TabSettings.set("tot_rounds", 1)
        url = reverse("judge_speaker_points")
        self.assertRedirects(self.client.get(url), f"/public/login/?next={url}",
                             fetch_redirect_response=False)

        staff = get_user_model().objects.create_user("helper", is_staff=True)
        self.client.force_login(staff)
        self.assertRedirects(
            self.client.get(url), "/403/", fetch_redirect_response=False,
        )

        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url).status_code, 405)
        self.admin.groups.add(Group.objects.create(name=APDA_BOARD_GROUP_NAME))
        self.assertRedirects(
            self.client.get(url), "/403/", fetch_redirect_response=False,
        )
