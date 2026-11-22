from django.test import TestCase

from mittab.apps.tab.models import CheckIn, Judge, Round, School, Scratch, TabSettings, Team
from mittab.libs import assign_judges


class TestRepeatJudgePenalty(TestCase):
    def setUp(self):
        super().setUp()
        school = School.objects.create(name="Test School")
        self.teams = [
            Team.objects.create(name=f"Team {index}", school=school, seed=Team.FULL_SEED)
            for index in range(4)
        ]
        self.debates = [self.make_round(2, index) for index in (0, 2)]
        TabSettings.set("cur_round", 3)
        TabSettings.set("allow_rejudges", True)
        TabSettings.set("rejudge_penalty", 0)

    def make_round(self, number, index):
        return Round.objects.create(
            round_number=number,
            gov_team=self.teams[index],
            opp_team=self.teams[index + 1],
        )

    def make_judge(self, name, rank, wing_only=False):
        judge = Judge.objects.create(name=name, rank=rank, wing_only=wing_only)
        CheckIn.objects.create(judge=judge, round_number=2)
        return judge

    def chairs(self):
        return list(
            Round.objects.filter(pk__in=[debate.pk for debate in self.debates])
            .order_by("pk").values_list("chair_id", flat=True)
        )

    def test_default_swaps_equal_chairs_and_zero_restores_original_assignment(self):
        self.make_judge("Chair A", 5)
        self.make_judge("Chair B", 5)
        TabSettings.set("repeat_judge_penalty", 0)
        assign_judges.add_judges()
        original_chairs = self.chairs()
        for index, chair_id in zip((0, 2), original_chairs):
            self.make_round(1, index).judges.add(chair_id)

        TabSettings.objects.get(key="repeat_judge_penalty").delete()
        self.assertEqual(assign_judges.get_inround_settings().repeat_judge_penalty, 1)
        assign_judges.add_judges()
        self.assertEqual(self.chairs(), list(reversed(original_chairs)))

        TabSettings.set("repeat_judge_penalty", 0)
        assign_judges.add_judges()
        self.assertEqual(self.chairs(), original_chairs)

    def test_weight_trades_off_repeat_wings_without_changing_chairs(self):
        top_chair = self.make_judge("Top chair", 5)
        second_chair = self.make_judge("Second chair", 4)
        wing_a = self.make_judge("Wing A", 3, wing_only=True)
        wing_b = self.make_judge("Wing B", 3, wing_only=True)
        Scratch.objects.create(judge=top_chair, team=self.teams[2], scratch_type=Scratch.TAB_SCRATCH)
        Scratch.objects.create(judge=second_chair, team=self.teams[0], scratch_type=Scratch.TAB_SCRATCH)

        first_history = self.make_round(1, 0)
        first_history.chair = second_chair
        first_history.save()
        first_history.judges.add(second_chair, wing_a, top_chair)
        second_history = self.make_round(1, 2)
        second_history.chair = top_chair
        second_history.save()
        second_history.judges.add(top_chair, wing_b)

        TabSettings.set("repeat_wing_penalty", 10)
        TabSettings.set("repeat_judge_penalty", 1)
        assign_judges.add_judges()
        self.assertEqual(self.chairs(), [top_chair.id, second_chair.id])
        self.assertSetEqual(
            set(self.debates[0].judges.all()), {top_chair, wing_a}
        )
        self.assertSetEqual(
            set(self.debates[1].judges.all()), {second_chair, wing_b}
        )

        TabSettings.set("repeat_judge_penalty", 10_000)
        assign_judges.add_judges()
        self.assertEqual(self.chairs(), [top_chair.id, second_chair.id])
        self.assertSetEqual(
            set(self.debates[0].judges.all()), {top_chair, wing_b}
        )
        self.assertSetEqual(
            set(self.debates[1].judges.all()), {second_chair, wing_a}
        )
