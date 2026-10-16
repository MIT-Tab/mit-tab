from decimal import Decimal

from django.db.models import Count
from django.test import TestCase
import pytest

from mittab.apps.tab.forms import JudgeForm
from mittab.apps.tab.models import CheckIn, Judge, Round, School, TabSettings, Team
from mittab.libs import assign_judges


@pytest.mark.django_db
class TestJudgeWingPreferences(TestCase):

    def setUp(self):
        super().setUp()
        TabSettings.set("cur_round", 2)
        TabSettings.set("tot_rounds", 5)
        TabSettings.set("pair_wings", 1)
        TabSettings.set(
            "wing_pairing_mode",
            assign_judges.WingPairingMode.WING_LEARNING,
        )
        TabSettings.set("allow_rejudges", 0)

        school = School.objects.create(name="Wing Preference School")
        teams = [
            Team.objects.create(
                name=f"Wing Preference Team {index}",
                school=school,
                seed=Team.UNSEEDED,
            )
            for index in range(4)
        ]
        Round.objects.create(
            round_number=1,
            gov_team=teams[0],
            opp_team=teams[1],
        )
        Round.objects.create(
            round_number=1,
            gov_team=teams[2],
            opp_team=teams[3],
        )

        judges = [
            Judge.objects.create(
                name=f"Wing Preference Judge {index}",
                rank=Decimal("5.00") - Decimal(index),
            )
            for index in range(3)
        ]
        CheckIn.objects.bulk_create(
            CheckIn(judge=judge, round_number=1) for judge in judges
        )

    def _rounds_by_panel_size(self):
        rounds = list(
            Round.objects.filter(round_number=1)
            .annotate(panel_size=Count("judges"))
            .order_by("id")
        )
        return {round_obj.panel_size: round_obj for round_obj in rounds}

    def test_request_wing_prioritizes_the_chairs_room(self):
        assign_judges.add_judges()
        rounds = self._rounds_by_panel_size()
        requested_round_id = rounds[1].id
        requested_chair = rounds[1].chair
        requested_chair.request_wing = True
        requested_chair.save(update_fields=["request_wing"])

        assign_judges.add_judges()

        requested_round = Round.objects.annotate(panel_size=Count("judges")).get(
            id=requested_round_id
        )
        self.assertEqual(requested_round.panel_size, 2)

    def test_no_wings_deprioritizes_the_chairs_room(self):
        assign_judges.add_judges()
        rounds = self._rounds_by_panel_size()
        no_wings_round_id = rounds[2].id
        other_round_id = rounds[1].id
        no_wings_chair = rounds[2].chair
        no_wings_chair.no_wings = True
        no_wings_chair.save(update_fields=["no_wings"])

        assign_judges.add_judges()

        updated_rounds = {
            round_obj.id: round_obj.panel_size
            for round_obj in Round.objects.filter(
                id__in=[no_wings_round_id, other_round_id]
            ).annotate(panel_size=Count("judges"))
        }
        self.assertEqual(updated_rounds[no_wings_round_id], 1)
        self.assertEqual(updated_rounds[other_round_id], 2)

    def test_chair_preferences_apply_the_expected_weight(self):
        request_wing_judge = Judge(request_wing=True)
        no_wings_judge = Judge(no_wings=True)
        neutral_judge = Judge()

        self.assertEqual(
            assign_judges.chair_wing_preference_weight(request_wing_judge),
            100,
        )
        self.assertEqual(
            assign_judges.chair_wing_preference_weight(no_wings_judge),
            -100,
        )
        self.assertEqual(
            assign_judges.chair_wing_preference_weight(neutral_judge),
            0,
        )

    def test_preferences_are_mutually_exclusive_in_the_judge_form(self):
        form = JudgeForm(
            data={
                "name": "Conflicted Judge",
                "rank": "3.00",
                "request_wing": True,
                "no_wings": True,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            "A judge cannot both request a wing and request no wings.",
            form.non_field_errors(),
        )
