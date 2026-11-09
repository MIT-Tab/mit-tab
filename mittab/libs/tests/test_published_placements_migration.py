import json
from importlib import import_module

from django.core.cache import caches
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from mittab.apps.tab.live_results import published_team_placements
from mittab.apps.tab.models import PublishedOutroundStage, PublishedTeamPlacement


class TestPublishedPlacementsMigration(TransactionTestCase):
    old_target = [("tab", "0054_judge_pairing_audit_event")]
    new_target = [("tab", "0056_backfill_published_placements")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.old_target)
        self.apps = executor.loader.project_state(self.old_target).apps
        self.settings = self.apps.get_model("tab", "TabSettings")
        self.rounds = self.apps.get_model("tab", "Outround")
        school = self.apps.get_model("tab", "School").objects.create(
            name="Migration school",
        )
        team_model = self.apps.get_model("tab", "Team")
        self.teams = [team_model.objects.create(
            name=f"Migration team {index}", school=school, seed=0,
        ) for index in range(16)]
        self.addCleanup(self.restore_schema)

    def restore_schema(self):
        MigrationExecutor(connection).migrate(self.new_target)
        for alias in ("default", "filesystem"):
            caches[alias].clear()

    def make_round(self, size, gov, opp, victor=1, division=0):
        return self.rounds.objects.create(
            num_teams=size, type_of_round=division,
            gov_team=self.teams[gov], opp_team=self.teams[opp], victor=victor,
        )

    def test_recovers_manual_rounds_and_preserves_every_pairing_field(self):
        # Same failure shape as the supplied SQL: only R16 was saved, while
        # quarters, semis and the final were entered manually and made public.
        legacy = {str(team.pk): 9 for team in self.teams}
        self.settings.objects.create(
            key="live_results_published_stages_0",
            value_string=json.dumps({"16": legacy}),
        )
        self.settings.objects.create(key="var_teams_visible", value=2)
        for index in range(8):
            self.make_round(16, index * 2, index * 2 + 1)
        for index in range(4):
            self.make_round(8, index * 4, index * 4 + 2)
        self.make_round(4, 0, 4)
        self.make_round(4, 8, 12)
        self.make_round(2, 0, 8, victor=2)
        judge = self.apps.get_model("tab", "Judge").objects.create(
            name="Assigned judge", rank=1,
        )
        final = self.rounds.objects.get(num_teams=2)
        final.chair = judge
        final.save()
        final.judges.add(judge)
        before = list(self.rounds.objects.order_by("pk").values())
        panels_before = list(self.rounds.judges.through.objects.values())

        self.restore_schema()

        self.assertEqual(PublishedOutroundStage.objects.count(), 4)
        expected = {team.pk: 9 for team in self.teams}
        for index in range(0, 16, 2):
            expected[self.teams[index].pk] = 5
        for index in range(0, 16, 4):
            expected[self.teams[index].pk] = 3
        expected[self.teams[0].pk] = 2
        expected[self.teams[8].pk] = 1
        self.assertEqual(published_team_placements(0), expected)
        self.assertEqual(list(self.rounds.objects.order_by("pk").values()), before)
        self.assertEqual(
            list(self.rounds.judges.through.objects.values()), panels_before,
        )
        # Re-running the data migration is harmless and never duplicates rows.
        migration = import_module(
            "mittab.apps.tab.migrations.0056_backfill_published_placements"
        )
        apps = MigrationExecutor(connection).loader.project_state(self.new_target).apps
        with connection.schema_editor() as editor:
            migration.backfill_placements(apps, editor)
        self.assertEqual(PublishedTeamPlacement.objects.count(), 30)
        self.assertEqual(published_team_placements(0), expected)

    def test_preserves_hidden_legacy_stages_without_publishing_private_results(self):
        self.settings.objects.create(key="var_teams_visible", value=256)
        self.settings.objects.create(
            key="live_results_published_stages_0",
            value_string=json.dumps({"16": {str(self.teams[0].pk): 5}}),
        )
        self.make_round(4, 2, 3)
        self.make_round(2, 2, 4)
        self.make_round(2, 5, 6, division=1)

        self.restore_schema()

        self.assertEqual(published_team_placements(0), {self.teams[0].pk: 5})
        self.assertEqual(published_team_placements(1), {})
        self.assertEqual(PublishedOutroundStage.objects.count(), 1)

    def test_recovers_corrupt_legacy_json_and_handles_byes_and_forfeits(self):
        self.settings.objects.create(key="var_teams_visible", value=16)
        self.settings.objects.create(key="nov_teams_visible", value=2)
        self.settings.objects.create(
            key="live_results_published_stages_0", value_string='{"16":',
        )
        self.settings.objects.create(
            key="live_results_published_stages_1",
            value_string=json.dumps({"4": {
                str(self.teams[5].pk): 3, "9999999": 1,
                str(self.teams[6].pk): "bad", "judge_rank": 1,
            }, "invalid": {}, "8": []}),
        )
        self.make_round(16, 0, 1, victor=3)
        for team in self.teams[:3]:
            self.apps.get_model("tab", "BreakingTeam").objects.create(team=team)
        self.make_round(2, 3, 4, victor=4, division=1)

        self.restore_schema()

        self.assertEqual(published_team_placements(0), {
            self.teams[0].pk: 5, self.teams[1].pk: 9, self.teams[2].pk: 5,
        })
        self.assertEqual(published_team_placements(1), {
            self.teams[3].pk: 2, self.teams[4].pk: 1, self.teams[5].pk: 3,
        })
