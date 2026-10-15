from unittest.mock import patch

# pylint: disable=protected-access

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from mittab.apps.tab.forms import OutroundResultEntryForm
from mittab.apps.tab.models import (
    BreakingTeam, Outround, OutroundChallenge, Room, RoomCheckIn, TabSettings,
)
from mittab.libs import outround_tab_logic
from mittab.libs.cacheing import cache_logic
from mittab.libs.outround_tab_logic import pairing


@override_settings(CACHES={
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "challenge-outs-tests-default",
    },
    "filesystem": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "challenge-outs-tests-persistent",
    },
    "public": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "challenge-outs-tests-public",
    },
})
class TestExperimentalChallengeOuts(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        cache_logic.clear_cache()
        TabSettings.set("cur_round", 6)
        RoomCheckIn.objects.bulk_create([
            RoomCheckIn(room=room, round_number=0) for room in Room.objects.all()
        ])
        self.user = get_user_model().objects.create_superuser(
            "challenge-review", "review@example.com", "password",
        )
        self.client.force_login(self.user)

    def perform_break(self, mode=1):
        TabSettings.set("outround_pairing_mode", mode)
        success, message = outround_tab_logic.perform_the_break()
        self.assertTrue(success, message)

    def teams(self):
        return list(BreakingTeam.objects.filter(
            type_of_team=BreakingTeam.VARSITY,
        ).order_by("seed"))

    def pairs(self, size):
        return sorted(tuple(sorted((one, two))) for one, two in
                      Outround.objects.filter(type_of_round=0, num_teams=size).values_list(
                          "gov_team_id", "opp_team_id"))

    def result(self, round_obj, upset=False):
        gov_seed = round_obj.gov_team.breaking_team.effective_seed
        opp_seed = round_obj.opp_team.breaking_team.effective_seed
        winner = Outround.GOV if (gov_seed < opp_seed) != upset else Outround.OPP
        form = OutroundResultEntryForm(
            {"winner": winner, "round_instance": round_obj.pk}, round_instance=round_obj,
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_disabled_ignores_stored_picks_and_uses_standard_code(self):
        self.perform_break(0)
        baseline = self.pairs(8)
        teams = self.teams()
        OutroundChallenge.objects.create(
            num_teams=8, type_of_round=0,
            challenger=teams[0].team, challenged=teams[1].team,
        )
        Outround.objects.filter(type_of_round=0).delete()
        with patch.object(pairing, "_pair_experimental") as experimental, \
                patch.object(pairing.OutroundChallenge.objects, "filter") as query:
            outround_tab_logic.pair(0)
            self.assertEqual(self.pairs(8), baseline)
            self.assertEqual(
    pairing.get_challenge_setup_context(
        0, 8), {
            "enabled": False})
            experimental.assert_not_called()
            query.assert_not_called()
        with self.assertRaisesRegex(ValueError, "not available"):
            pairing.save_challenge_pick(0, 8, teams[0].team_id, teams[1].team_id)

    def test_initial_challenge_once_waits_for_staff_picks(self):
        self.perform_break(2)
        self.assertFalse(Outround.objects.exists())
        teams = self.teams()
        pairing.save_challenge_pick(0, 8, teams[0].team_id, teams[1].team_id)
        outround_tab_logic.pair(0)
        self.assertIn(
    tuple(
        sorted(
            (teams[0].team_id,
            teams[1].team_id))),
             self.pairs(8))
        self.assertEqual(
    Outround.objects.filter(
        type_of_round=0,
        num_teams=8).count(),
         4)
        self.assertFalse(pairing.get_challenge_setup_context(0, 4)["enabled"])

    def test_initial_setup_is_reachable_and_marked_experimental(self):
        self.perform_break(2)
        response = self.client.get(reverse("outround_pairing_view_default"), follow=True)
        self.assertContains(response, "Experimental:")
        url = reverse("next_outround", kwargs={
            "num_teams": 16, "type_of_round": 0,
        })
        self.assertContains(response, url)
        response = self.client.get(url)
        self.assertContains(response, "Challenge Outs")
        self.assertContains(response, "Seed 1 starts")
        self.assertNotContains(response, "Pre-pairing checks")

        response = self.client.get(f"{url}?stage=pairing")
        self.assertContains(response, "Pre-pairing checks")
        self.assertNotContains(response, "Recorded matchups")

    def test_setup_context_guides_staff_through_available_teams(self):
        self.perform_break(1)
        teams = self.teams()

        context = pairing.get_challenge_setup_context(0, 8)
        self.assertEqual(context["next_challenger"]["id"], teams[0].team_id)
        self.assertNotIn(
            teams[0].team_id,
            {option["id"] for option in context["opponent_options"]},
        )

        pairing.save_challenge_pick(0, 8, teams[0].team_id, teams[2].team_id)
        context = pairing.get_challenge_setup_context(0, 8)

        self.assertEqual(context["next_challenger"]["id"], teams[1].team_id)
        self.assertEqual(
            context["saved_challenges"][0]["challenged"]["id"],
            teams[2].team_id,
        )
        remaining_ids = {option["id"] for option in context["remaining_teams"]}
        self.assertNotIn(teams[0].team_id, remaining_ids)
        self.assertNotIn(teams[2].team_id, remaining_ids)

    def test_staff_can_remove_a_saved_challenge(self):
        self.perform_break(1)
        teams = self.teams()
        pairing.save_challenge_pick(0, 8, teams[0].team_id, teams[2].team_id)

        response = self.client.post(reverse("next_outround", kwargs={
            "num_teams": 16, "type_of_round": 0,
        }), {
            "action": "delete_challenge",
            "challenger_id": teams[0].team_id,
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(OutroundChallenge.objects.exists())

    def test_once_upsets_keep_bracket_progression_complete(self):
        self.perform_break(2)
        outround_tab_logic.pair(0)
        for size in (8, 4, 2):
            rounds = list(Outround.objects.filter(type_of_round=0, num_teams=size))
            self.assertEqual(len(rounds), size // 2)
            active_ids = set()
            for round_obj in rounds:
                self.assertEqual(
                    round_obj.gov_team.breaking_team.effective_seed
                    + round_obj.opp_team.breaking_team.effective_seed,
                    size + 1,
                )
                result = self.result(round_obj, upset=True)
                active_ids.add(result.winner.pk)
            if size > 2:
                outround_tab_logic.pair(0)
                paired_ids = {team for pair in self.pairs(size // 2) for team in pair}
                self.assertEqual(paired_ids, active_ids)

    def test_partial_break_keeps_standard_first_round(self):
        TabSettings.set("var_teams_to_break", 7)
        self.perform_break(1)
        self.assertEqual(
    Outround.objects.filter(
        type_of_round=0,
        num_teams=8).count(),
         3)
        self.assertFalse(pairing.get_challenge_setup_context(0, 8)["enabled"])
        for round_obj in Outround.objects.filter(type_of_round=0, num_teams=8):
            self.result(round_obj)
        self.assertTrue(pairing.get_challenge_setup_context(0, 4)["enabled"])
        outround_tab_logic.pair(0)
        self.assertEqual(len(self.pairs(4)), 2)

    def test_overlapping_self_and_cross_break_picks_are_rejected(self):
        self.perform_break(1)
        teams = self.teams()
        pairing.save_challenge_pick(0, 8, teams[0].team_id, teams[1].team_id)
        for one, two in [(1, 2), (2, 0), (0, 0)]:
            with self.assertRaises(ValueError):
                pairing.save_challenge_pick(
    0, 8, teams[one].team_id, teams[two].team_id)
        novice = BreakingTeam.objects.filter(type_of_team=1).first()
        with self.assertRaises(ValueError):
            pairing.save_challenge_pick(0, 8, teams[2].team_id, novice.team_id)
        self.assertEqual(OutroundChallenge.objects.count(), 1)

    def test_pick_can_be_changed_without_duplicate_team_participation(self):
        self.perform_break(1)
        teams = self.teams()
        pairing.save_challenge_pick(0, 8, teams[0].team_id, teams[1].team_id)
        pairing.save_challenge_pick(0, 8, teams[0].team_id, teams[2].team_id)
        outround_tab_logic.pair(0)
        paired_ids = [team for pair in self.pairs(8) for team in pair]
        self.assertEqual(len(set(paired_ids)), 8)
        self.assertIn(
    tuple(
        sorted(
            (teams[0].team_id,
            teams[2].team_id))),
             self.pairs(8))

    def test_invalid_pick_failure_preserves_existing_pairings(self):
        self.perform_break(1)
        teams = self.teams()
        outround_tab_logic.pair(0)
        baseline = self.pairs(8)
        TabSettings.set("challenge_pick_generation_0", pairing._challenge_generation(0))
        OutroundChallenge.objects.create(
            num_teams=8, type_of_round=0,
            challenger=teams[0].team, challenged=teams[1].team,
        )
        OutroundChallenge.objects.create(
            num_teams=8, type_of_round=0,
            challenger=teams[1].team, challenged=teams[2].team,
        )
        with self.assertRaises(ValueError):
            outround_tab_logic.pair(0)
        self.assertEqual(self.pairs(8), baseline)

    def test_disabled_setup_post_cannot_modify_picks(self):
        self.perform_break(0)
        response = self.client.post(reverse("next_outround", kwargs={
            "num_teams": 16, "type_of_round": 0,
        }), {
            "action": "set_challenge", "challenger_id": 1, "challenged_id": 2,
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(OutroundChallenge.objects.exists())

    def test_finals_do_not_offer_challenge_picks(self):
        self.perform_break(1)
        self.assertFalse(pairing.get_challenge_setup_context(0, 2)["enabled"])


    def test_rebreak_while_disabled_does_not_reuse_old_experimental_state(self):
        self.perform_break(2)
        teams = self.teams()
        pairing.save_challenge_pick(0, 8, teams[0].team_id, teams[1].team_id)
        outround_tab_logic.pair(0)
        old_generation = pairing._challenge_generation(0)
        self.perform_break(0)
        TabSettings.set("outround_pairing_mode", 2)
        self.assertNotEqual(pairing._challenge_generation(0), old_generation)
        context = pairing.get_challenge_setup_context(0, 8)
        self.assertTrue(context["enabled"])
        self.assertTrue(all(item["challenged_id"] is None for item in context["challenges"]))
        teams = self.teams()
        pairing.save_challenge_pick(0, 8, teams[0].team_id, teams[2].team_id)
        self.assertEqual(OutroundChallenge.objects.filter(type_of_round=0).count(), 1)
        outround_tab_logic.pair(0)
        self.assertIn(tuple(sorted((teams[0].team_id, teams[2].team_id))), self.pairs(8))


    def test_admin_pick_belongs_to_current_break(self):
        self.perform_break(1)
        teams = self.teams()
        response = self.client.post(reverse("admin:tab_outroundchallenge_add"), {
            "num_teams": 8, "type_of_round": 0,
            "challenger": teams[0].team_id, "challenged": teams[1].team_id,
            "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        outround_tab_logic.pair(0)
        self.assertIn(tuple(sorted((teams[0].team_id, teams[1].team_id))), self.pairs(8))
