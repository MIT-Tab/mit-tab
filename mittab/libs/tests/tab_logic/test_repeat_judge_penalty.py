from django.test import TestCase

from mittab.apps.tab.models import CheckIn, Judge, Round, School, TabSettings, Team
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

    def make_judge(self, name, rank):
        judge = Judge.objects.create(name=name, rank=rank)
        CheckIn.objects.create(judge=judge, round_number=2)
        return judge

    def chairs(self):
        return list(
            Round.objects.filter(pk__in=[debate.pk for debate in self.debates])
            .order_by("pk").values_list("chair_id", flat=True)
        )

    def test_repeat_judge_penalty_does_not_change_inround_assignments(self):
        self.make_judge("Chair A", 5)
        self.make_judge("Chair B", 5)
        TabSettings.set("repeat_judge_penalty", 0)
        assign_judges.add_judges()
        original_chairs = self.chairs()
        for index, chair_id in zip((0, 2), original_chairs):
            self.make_round(1, index).judges.add(chair_id)

        TabSettings.set("repeat_judge_penalty", 1000)
        assign_judges.add_judges()

        self.assertEqual(self.chairs(), original_chairs)
