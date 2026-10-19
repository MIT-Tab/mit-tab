from decimal import Decimal

from django.test import TestCase
import pytest

from mittab.apps.tab.models import CheckIn, Judge, Round, School, TabSettings, Team
from mittab.libs import assign_judges


@pytest.mark.django_db
class TestRepeatWingPenalty(TestCase):
    def make_team(self, name, school, seed):
        return Team.objects.create(
            name=name,
            school=school,
            seed=seed,
        )

    def make_judge(self, name, rank, wing_only=False):
        return Judge.objects.create(
            name=name,
            rank=Decimal(rank),
            wing_only=wing_only,
        )

    def test_inround_settings_default_repeat_wing_penalty_to_ten(self):
        TabSettings.objects.filter(key="repeat_wing_penalty").delete()

        settings = assign_judges.get_inround_settings()

        self.assertEqual(settings.repeat_wing_penalty, 10)

    def test_counts_only_prior_rounds_where_judge_was_a_wing(self):
        school = School.objects.create(name="History School")
        teams = [
            self.make_team(f"History Team {index}", school, index)
            for index in range(1, 5)
        ]
        chair = self.make_judge("History Chair", "5.00")
        wing = self.make_judge("History Wing", "4.00", wing_only=True)

        prior_round = Round.objects.create(
            round_number=1,
            gov_team=teams[0],
            opp_team=teams[1],
            chair=chair,
        )
        prior_round.judges.add(chair, wing)
        current_round = Round.objects.create(
            round_number=2,
            gov_team=teams[2],
            opp_team=teams[3],
            chair=wing,
        )
        current_round.judges.add(wing)

        prefetched_wing = Judge.objects.prefetch_related("judges").get(pk=wing.pk)
        counts = assign_judges.judge_chair_wing_counts(
            [prefetched_wing],
            before_round_number=2,
        )

        self.assertEqual(counts, {wing.id: {chair.id: 1}})

    def test_assignment_avoids_repeating_chair_wing_pairs(self):
        school = School.objects.create(name="Pairing School")
        teams = [
            self.make_team(f"Pairing Team {index}", school, index)
            for index in range(1, 9)
        ]
        chair_a = self.make_judge("Chair A", "5.00")
        chair_b = self.make_judge("Chair B", "4.00")
        wing_a = self.make_judge("Wing A", "3.00", wing_only=True)
        wing_b = self.make_judge("Wing B", "2.00", wing_only=True)

        prior_a = Round.objects.create(
            round_number=1,
            gov_team=teams[0],
            opp_team=teams[1],
            chair=chair_a,
        )
        prior_a.judges.add(chair_a, wing_a)
        prior_b = Round.objects.create(
            round_number=1,
            gov_team=teams[2],
            opp_team=teams[3],
            chair=chair_b,
        )
        prior_b.judges.add(chair_b, wing_b)

        Round.objects.create(
            round_number=2,
            gov_team=teams[4],
            opp_team=teams[5],
        )
        Round.objects.create(
            round_number=2,
            gov_team=teams[6],
            opp_team=teams[7],
        )
        CheckIn.objects.bulk_create(
            CheckIn(judge=judge, round_number=2)
            for judge in (chair_a, chair_b, wing_a, wing_b)
        )
        TabSettings.set("cur_round", 3)
        TabSettings.set("pair_wings", 1)
        TabSettings.set(
            "wing_pairing_mode",
            assign_judges.WingPairingMode.WING_LEARNING,
        )
        TabSettings.set("repeat_wing_penalty", 10)
        TabSettings.set("allow_rejudges", 0)

        assign_judges.add_judges()

        expected_wings = {
            chair_a.id: wing_b.id,
            chair_b.id: wing_a.id,
        }
        current_rounds = Round.objects.filter(round_number=2).prefetch_related(
            "judges"
        )
        for round_obj in current_rounds:
            wing_ids = {
                judge.id
                for judge in round_obj.judges.all()
                if judge.id != round_obj.chair_id
            }
            self.assertEqual(wing_ids, {expected_wings[round_obj.chair_id]})
