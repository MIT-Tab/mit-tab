"""Parity, allocation, tournament and pre-feature compatibility coverage."""
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.urls import reverse

from mittab.apps.tab.forms import SettingsForm
from mittab.apps.tab.models import (
    Bye, CheckIn, NoShow, RoomCheckIn, Round, RoundStats, TabSettings, Team,
)
from mittab.libs import errors, tab_logic
from mittab.libs.cacheing import cache_logic


class TestMinByes(TestCase):
    fixtures = ["testing_db"]

    def setUp(self):
        cache_logic.clear_cache()
        TabSettings.set("cur_round", 1)
        self.teams = []

    def prepare(self, team_count, minimum):
        Team.objects.update(checked_in=False)
        self.teams = list(Team.objects.order_by("pk")[:team_count])
        Team.objects.filter(pk__in=[t.pk for t in self.teams]).update(checked_in=True)
        for team in self.teams:
            team.checked_in = True
        TabSettings.set("min_byes", minimum)
        # The fixture's resources are sufficient; check in for later rounds too.
        judge_ids = list(
            CheckIn.objects.values_list("judge_id", flat=True).distinct()
        )
        room_ids = list(
            RoomCheckIn.objects.values_list("room_id", flat=True).distinct()
        )
        CheckIn.objects.bulk_create([
            CheckIn(judge_id=judge_id, round_number=number)
            for number in range(1, 10) for judge_id in judge_ids
        ], ignore_conflicts=True)
        RoomCheckIn.objects.bulk_create([
            RoomCheckIn(room_id=room_id, round_number=number)
            for number in range(1, 10) for room_id in room_ids
        ], ignore_conflicts=True)

    def pair(self, number):
        TabSettings.set("cur_round", number)
        cache_logic.clear_cache()
        tab_logic.pair_round()
        byes = list(Bye.objects.filter(round_number=number)
                    .values_list("bye_team_id", flat=True))
        debates = list(Round.objects.filter(round_number=number).order_by("pk"))
        participants = byes + [
            pk for r in debates for pk in (r.gov_team_id, r.opp_team_id)
        ]
        self.assertCountEqual(participants, [t.pk for t in self.teams])
        self.assertEqual(len(participants), len(set(participants)))
        return byes, debates

    @staticmethod
    def enter_results(debates):
        """Stable results independent of Python's randomized name hashes."""
        for index, debate in enumerate(debates):
            debate.victor = Round.GOV if index % 2 else Round.OPP
            debate.save()
            for role, team in (("gov", debate.gov_team), ("opp", debate.opp_team)):
                for offset, debater in enumerate(team.debaters.order_by("pk")):
                    RoundStats.objects.create(
                        round=debate, debater=debater,
                        speaks=25 + (debater.pk % 5) / 2,
                        ranks=offset + (1 if role == "gov" else 3),
                        debater_role=("pm", "mg")[offset] if role == "gov"
                        else ("lo", "mo")[offset],
                    )

    def test_first_round_count_matrix(self):
        for size in range(0, 11):
            for minimum in range(1, size + 1):
                with self.subTest(size=size, minimum=minimum), transaction.atomic():
                    self.prepare(size, minimum)
                    byes, debates = self.pair(1)
                    expected = minimum + (size - minimum) % 2
                    self.assertEqual(len(byes), expected)
                    self.assertEqual(len(debates), (size - expected) // 2)
                    transaction.set_rollback(True)

    def test_repeated_rounds_exhaust_each_bye_tier(self):
        for size, minimum in ((8, 1), (9, 1), (8, 2), (9, 2), (8, 5), (9, 9)):
            with self.subTest(size=size, minimum=minimum), transaction.atomic():
                self.prepare(size, minimum)
                counts = dict.fromkeys([t.pk for t in self.teams], 0)
                expected = minimum + (size - minimum) % 2
                for number in range(1, 9):
                    byes, debates = self.pair(number)
                    self.assertEqual(len(byes), expected)
                    # No recipient can skip an eligible team in a lower tier.
                    for recipient in sorted(byes, key=counts.get):
                        eligible = [
                            pk for pk in counts if pk not in byes or pk == recipient
                        ]
                        self.assertEqual(
                            counts[recipient], min(counts[pk] for pk in eligible)
                        )
                        counts[recipient] += 1
                    self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
                    self.enter_results(debates)
                transaction.set_rollback(True)

    def test_fairness_precedes_standings_and_ignores_absent_teams(self):
        self.prepare(8, 2)
        for index, team in enumerate(self.teams):
            for previous in range(index % 3):
                Bye.objects.create(bye_team=team, round_number=previous + 10)
        # All lowest-tier teams must be considered before a repeat recipient.
        candidates = Team.with_preloaded_relations_for_tabbing().filter(checked_in=True)
        tab_logic.assign_minimum_byes(candidates, 2)
        self.assertEqual(Bye.objects.filter(round_number=2).count(), 2)
        for bye in Bye.objects.filter(round_number=2):
            self.assertEqual(self.teams.index(bye.bye_team) % 3, 0)

    def test_returning_team_gets_priority_for_fewer_byes(self):
        self.prepare(9, 8)
        returning = self.teams[0]
        returning.checked_in = False
        returning.save()
        self.teams = self.teams[1:]
        for number in (1, 2):
            self.pair(number)
        # Everyone present has now received two byes; the returning team has zero.
        returning.checked_in = True
        returning.save()
        self.teams.append(returning)
        TabSettings.set("min_byes", 1)
        byes, _ = self.pair(3)
        self.assertEqual(byes, [returning.pk])

    def test_repair_has_identical_recipients_and_pairings(self):
        self.prepare(9, 2)
        original_byes, original_debates = self.pair(1)
        original_pairs = [(r.gov_team_id, r.opp_team_id) for r in original_debates]
        for _ in range(3):
            Round.objects.filter(round_number=1).delete()
            Bye.objects.filter(round_number=1).delete()
            NoShow.objects.filter(round_number=1).delete()
            byes, debates = self.pair(1)
            self.assertEqual(byes, original_byes)
            self.assertEqual(
                [(r.gov_team_id, r.opp_team_id) for r in debates], original_pairs
            )

    def test_resource_checks_use_actual_debate_count(self):
        self.prepare(8, 6)
        CheckIn.objects.filter(round_number=1).exclude(
            pk=CheckIn.objects.filter(round_number=1).first().pk).delete()
        RoomCheckIn.objects.filter(round_number=1).exclude(
            pk=RoomCheckIn.objects.filter(round_number=1).first().pk).delete()
        self.assertEqual(tab_logic.have_enough_judges(1), (True, (1, 1)))
        self.assertEqual(tab_logic.have_enough_rooms(1), (True, (1, 1)))
        self.pair(1)

    def test_invalid_minimum_rejected_before_pairing_writes(self):
        for minimum in (-1, 9):
            with self.subTest(minimum=minimum):
                self.prepare(8, minimum)
                with self.assertRaises(errors.ByeAssignmentError):
                    tab_logic.pair_round()
                self.assertFalse(Round.objects.exists())
                self.assertFalse(Bye.objects.exists())
                self.assertFalse(NoShow.objects.exists())

    def test_form_validates_and_saves_minimum(self):
        cases = (("0", True), ("2", True), ("-1", False), ("1.5", False))
        for value, valid in cases:
            form = SettingsForm(
                {"setting_min_byes": value},
                settings=[{
                    "name": "min_byes", "value": 0, "description": "Minimum byes"
                }],
            )
            self.assertEqual(form.is_valid(), valid)
            if valid:
                form.save()
                self.assertEqual(TabSettings.get("min_byes", 0), int(value))

    def test_positive_minimum_respects_round_one_seed_policy(self):
        for policy in (0, 1, 2):
            with self.subTest(policy=policy), transaction.atomic():
                self.prepare(8, 2)
                TabSettings.set("fair_bye", policy)
                for index, team in enumerate(self.teams):
                    team.seed = Team.UNSEEDED if index < 2 else Team.FULL_SEED
                    team.save()
                byes, _ = self.pair(1)
                if policy:
                    self.assertCountEqual(byes, [t.pk for t in self.teams[:2]])
                transaction.set_rollback(True)

    def test_positive_minimum_still_requires_enough_resources(self):
        self.prepare(8, 2)
        CheckIn.objects.filter(round_number=1).delete()
        self.assertEqual(tab_logic.have_enough_judges(1), (False, (0, 3)))
        with self.assertRaises(errors.NotEnoughJudgesError):
            tab_logic.pair_round()
        RoomCheckIn.objects.all().delete()
        self.assertEqual(tab_logic.have_enough_rooms(1), (False, (0, 3)))

    def test_pairing_page_explains_minimum_exceeding_checkins(self):
        self.prepare(8, 9)
        user = get_user_model().objects.create_superuser(
            username="min-byes-admin", email="byes@example.com", password="test"
        )
        self.client.force_login(user)
        response = self.client.get(reverse("pair_round"), follow=True)
        self.assertRedirects(response, reverse("settings_form"))
        self.assertContains(response, "Min byes cannot exceed")

    def zero_snapshot(self, pair_round=None):
        if pair_round is None:
            pair_round = tab_logic.pair_round
        result = []
        for number in range(1, 6):
            TabSettings.set("cur_round", number)
            cache_logic.clear_cache()
            pair_round()
            debates = list(Round.objects.filter(round_number=number).order_by("pk"))
            result.append({
                "byes": list(Bye.objects.filter(round_number=number)
                             .values_list("bye_team_id", flat=True)),
                "pairs": [[r.gov_team_id, r.opp_team_id, r.pullup] for r in debates],
            })
            self.enter_results(debates)
        return result

    def test_zero_matches_pre_feature_tournaments(self):
        # Golden data generated from c41a23e's unmodified pairing module, using
        # zero_snapshot's deterministic results and the same testing_db fixture.
        golden = json.loads(
            Path(__file__).with_name("min_byes_legacy.json").read_text(encoding="utf-8")
        )
        for scenario, expected in golden.items():
            size, policy, random_r2 = map(int, scenario.split("-"))
            for explicit_zero in (False, True):
                with self.subTest(scenario=scenario, explicit_zero=explicit_zero), \
                        transaction.atomic():
                    self.prepare(size, 0)
                    if not explicit_zero:
                        TabSettings.objects.filter(key="min_byes").delete()
                    TabSettings.set("fair_bye", policy)
                    TabSettings.set("r2_random_within_brackets", random_r2)
                    self.assertEqual(self.zero_snapshot(), expected)
                    transaction.set_rollback(True)
