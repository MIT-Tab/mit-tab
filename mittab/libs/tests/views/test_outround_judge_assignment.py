"""Outround judge reassignment updates the published panels."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase
from django.urls import reverse

from mittab.apps.tab.models import (
    BreakingTeam, CheckIn, Judge, Outround, School, Scratch, TabSettings, Team,
)


class TestOutroundJudgeAssignment(TestCase):
    def test_assign_judges_replaces_cached_public_panels_in_both_divisions(self):
        cache = caches["public"]
        cache.clear()
        self.addCleanup(cache.clear)
        user = get_user_model().objects.create_superuser(username="tab")
        self.client.force_login(user)
        TabSettings.set("cur_round", 6)
        TabSettings.set("tot_rounds", 5)
        TabSettings.set("var_teams_visible", 2)
        TabSettings.set("nov_teams_visible", 2)
        TabSettings.set("var_panel_size", 1)
        TabSettings.set("nov_panel_size", 1)

        school = School.objects.create(name="Scratch Test School")
        old_judge = Judge.objects.create(name="Old Scratched Judge", rank=10)
        new_judges = [
            Judge.objects.create(name=f"Replacement Judge {rank}", rank=rank)
            for rank in (8, 9)
        ]
        for judge in [old_judge, *new_judges]:
            CheckIn.objects.create(judge=judge, round_number=0)

        public = Client()
        pairings = []
        for division in (Outround.VARSITY, Outround.NOVICE):
            gov, opp = [
                Team.objects.create(
                    name=f"Team {division}-{seed}", school=school, seed=0,
                )
                for seed in (1, 2)
            ]
            for seed, team in enumerate((gov, opp), start=1):
                BreakingTeam.objects.create(
                    team=team, seed=seed, effective_seed=seed, type_of_team=division,
                )
            pairing = Outround.objects.create(
                type_of_round=division, num_teams=2, gov_team=gov, opp_team=opp,
                chair=old_judge,
            )
            pairing.judges.add(old_judge)
            Scratch.objects.create(
                team=opp, judge=old_judge, scratch_type=Scratch.TAB_SCRATCH,
            )
            pairings.append(pairing)
            self.assertContains(
                public.get(reverse("outround_pretty_pair", args=[division])),
                old_judge.name,
                count=2,
            )

        with patch("mittab.apps.tab.views.outround_pairing_views.backup.backup_round"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("outround_assign_judges"),
                    {"selected_varsity": 2, "selected_novice": 2},
                )
        self.assertEqual(response.status_code, 302)

        assigned_judges = []
        for pairing in pairings:
            pairing.refresh_from_db()
            assigned_judges.append(pairing.chair)
            self.assertEqual(list(pairing.judges.all()), [pairing.chair])
            page = public.get(reverse(
                "outround_pretty_pair", args=[pairing.type_of_round],
            ))
            self.assertNotContains(page, old_judge.name)
            self.assertContains(page, pairing.chair.name, count=2)
        self.assertCountEqual(assigned_judges, new_judges)
