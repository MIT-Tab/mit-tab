"""Query budgets for the cold paths reported by production N+1 monitoring."""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.cache import caches
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from mittab.apps.tab.board_audit import (
    audit_pairing_generation, pairing_snapshot, with_pairing_snapshot_relations,
)
from mittab.apps.tab.models import (
    AuditEvent, BreakingTeam, Debater, Judge, Outround, Room, Round, RoundStats,
    School, Scratch, TabSettings, Team,
)
from mittab.apps.tab.views.api_views import _serialize_live_debater
from mittab.apps.tab.views.board_views import pairing_rows
from mittab.apps.tab.views.debater_views import get_speaker_rankings
from mittab.libs.outround_tab_logic.checks import lost_teams
from mittab.libs.outround_tab_logic.pairing import (
    _breaking_teams_by_effective_seed, gov_team,
)
from mittab.libs.cacheing.public_cache import PUBLIC_CACHE_ALIAS


class TestQueryLoading(TestCase):
    def setUp(self):
        caches["default"].clear()
        caches["filesystem"].clear()
        caches[PUBLIC_CACHE_ALIAS].clear()
        self.school = School.objects.create(name="School", apda_id=123)
        self.member_school = School.objects.create(name="Member school", apda_id=456)
        self.judge = Judge.objects.create(name="Judge", rank=1)
        self.room = Room.objects.create(name="Room", rank=1)
        self.teams = []
        for seed in range(1, 9):
            team = Team.objects.create(name=f"Team {seed}", school=self.school, seed=0)
            for member in range(2):
                debater = Debater.objects.create(
                    name=f"Speaker {seed}-{member}", novice_status=member,
                    school=self.member_school if member else None,
                )
                team.debaters.add(debater)
            BreakingTeam.objects.create(team=team, seed=seed, effective_seed=seed)
            self.teams.append(team)
        for index in range(4):
            pairing = Round.objects.create(
                gov_team=self.teams[index], opp_team=self.teams[7 - index],
                round_number=1, room=self.room, chair=self.judge, victor=Round.GOV,
            )
            pairing.judges.add(self.judge)
            RoundStats.objects.create(
                round=pairing, debater=pairing.gov_team.debaters.first(),
                debater_role="pm", speaks=27, ranks=1,
            )
            AuditEvent.record(pairing, AuditEvent.MANUAL_JUDGE_ASSIGN,
                              changes={"assigned_judge": self.judge.name})
        TabSettings.set("tot_rounds", 1)
        TabSettings.set("cur_round", 2)
        TabSettings.set("var_teams_visible", 8)
        TabSettings.set("debaters_public", 1)

    def _outround(self, index=0, **kwargs):
        pairing = Outround.objects.create(
            gov_team=self.teams[index], opp_team=self.teams[7 - index],
            room=self.room, chair=self.judge, num_teams=8, **kwargs,
        )
        pairing.judges.add(self.judge)
        return pairing

    def test_board_rows_use_constant_queries_and_match_individual_snapshots(self):
        ContentType.objects.get_for_model(Round)
        queryset = Round.objects.order_by("id")
        expected = [pairing_snapshot(pairing) for pairing in queryset]
        with self.assertNumQueries(5):
            single = pairing_rows(queryset.all()[:1])
        with self.assertNumQueries(5):
            rows = pairing_rows(queryset.all())
        self.assertEqual(single, rows[:1])
        for snapshot, row in zip(expected, rows):
            self.assertEqual(snapshot, {key: row[key] for key in snapshot})
            self.assertEqual(row["manual_judge_names"], [self.judge.name])
            self.assertTrue(row["history"])

    def test_bulk_audit_snapshots_have_no_lazy_queries(self):
        for index in range(4):
            self._outround(index)
        for model, budget in ((Round, 3), (Outround, 2)):
            expected = [pairing_snapshot(pairing)
                        for pairing in model.objects.order_by("pk")]
            with self.assertNumQueries(budget):
                pairings = list(with_pairing_snapshot_relations(
                    model.objects.order_by("pk")))
            with self.assertNumQueries(0):
                actual = [pairing_snapshot(pairing) for pairing in pairings]
            self.assertEqual(expected, actual)

    def test_bulk_assignment_audit_reads_are_bounded_and_see_updated_panels(self):
        request = RequestFactory().post("/pairing/assign_judges/")
        request.user = AnonymousUser()
        ContentType.objects.get_for_model(Round)
        view = audit_pairing_generation(Round, "staff judge assignment")(
            lambda request: HttpResponse())
        with self.assertNumQueries(6):
            view(request)

        pairing = Round.objects.first()

        @audit_pairing_generation(Round, "staff judge assignment")
        def remove_panel(request):
            pairing.judges.clear()
            return HttpResponse()

        remove_panel(request)
        event = AuditEvent.objects.filter(event_type=AuditEvent.EDIT).get()
        self.assertEqual(event.changes["public"]["before"]["judges"],
                         [{"id": self.judge.pk, "name": self.judge.name}])
        self.assertEqual(event.changes["public"]["after"]["judges"], [])

    def test_public_outrounds_queries_do_not_grow_with_pairings_or_stages(self):
        self._outround()
        url = reverse("outround_pretty_pair", args=[0])

        def read_page():
            # Exercise bracket generation on every measurement, with cold caches.
            caches["default"].clear()
            caches["filesystem"].clear()
            caches[PUBLIC_CACHE_ALIAS].clear()
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            # Ignore session/settings initialization writes; budget the data reads.
            reads = sum(query["sql"].lstrip().upper().startswith("SELECT")
                        for query in queries)
            return response, reads

        _, single_queries = read_page()
        for index in range(1, 4):
            self._outround(index)
        response, full_queries = read_page()
        self.assertEqual(single_queries, full_queries)
        self.assertEqual(len(response.context["outround_pairings"][0]["rounds"]), 4)
        bracket = json.loads(response.context["bracket_data_json"])
        self.assertEqual(len(bracket["participants"]), 8)
        self.assertEqual(len(bracket["match_metadata"]), 4)
        self.assertIn("Speaker 1-0", response.content.decode())

        # Adding a later visible stage must not add another set of per-team queries.
        TabSettings.set("var_teams_visible", 4)
        Outround.objects.create(
            gov_team=self.teams[0], opp_team=self.teams[1], num_teams=4)
        response, multi_stage_queries = read_page()
        self.assertEqual(full_queries, multi_stage_queries)
        self.assertEqual(len(response.context["outround_pairings"]), 2)

    def test_advancing_teams_exclude_eliminated_and_currently_paired_teams(self):
        self._outround(victor=Outround.GOV)
        Outround.objects.create(
            gov_team=self.teams[1], opp_team=self.teams[2], num_teams=4,
            victor=Outround.OPP_VIA_FORFEIT,
        )
        response = self.client.get(reverse("outround_pretty_pair", args=[0]))
        excluded = response.context["outround_pairings"][0]["excluded"]
        self.assertEqual([team.pk for team in excluded],
                         [team.pk for team in self.teams[2:7]])

    def test_live_speaker_schools_are_loaded_before_serialization(self):
        speakers, _ = get_speaker_rankings()
        with self.assertNumQueries(0):
            rows = [_serialize_live_debater(entry[0], entry[3]) for entry in speakers]
        self.assertEqual({row["school"]["apda_id"] for row in rows}, {123, 456})

    def test_eliminated_ids_use_one_query_for_all_outcomes_and_divisions(self):
        outcomes = (Outround.UNKNOWN, Outround.GOV, Outround.OPP,
                    Outround.GOV_VIA_FORFEIT, Outround.OPP_VIA_FORFEIT)
        for outcome in outcomes:
            self._outround(victor=outcome, type_of_round=outcome % 2)
        expected = [pairing.loser.pk for pairing in Outround.objects.all()
                    if pairing.loser]
        with self.assertNumQueries(1):
            self.assertCountEqual(lost_teams(), expected)
        with self.assertNumQueries(1):
            self.assertCountEqual(lost_teams(Outround.NOVICE), [self.teams[7].pk] * 2)

    def test_outround_seed_and_side_loading_is_bounded(self):
        TabSettings.set("sidelock", 1)
        TabSettings.get("sidelock")
        with self.assertNumQueries(3):
            by_seed = _breaking_teams_by_effective_seed(BreakingTeam.objects.all())
        with self.assertNumQueries(0):
            for seed in range(1, 5):
                sidelock, gov = gov_team(by_seed[seed], by_seed[9 - seed])
                self.assertTrue(sidelock)
                self.assertEqual(gov.team, self.teams[8 - seed])

    def test_scratch_list_queries_do_not_grow_with_scratches(self):
        Scratch.objects.create(team=self.teams[0], judge=self.judge, scratch_type=0)
        url = reverse("view_scratches")
        # Exercise the staff capability check in middleware.
        self.client.force_login(get_user_model().objects.create_superuser(
            "tab", password="test"))

        def read_page():
            caches["default"].clear()
            caches["filesystem"].clear()
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            return sum(query["sql"].lstrip().upper().startswith("SELECT")
                       for query in queries)

        single_queries = read_page()
        for team in self.teams[1:]:
            Scratch.objects.create(team=team, judge=self.judge, scratch_type=0)
        self.assertEqual(single_queries, read_page())

    def test_staff_outrounds_queries_do_not_grow_with_pairings(self):
        ContentType.objects.get_for_model(Outround)
        self.client.force_login(get_user_model().objects.create_superuser(
            "tab", password="test"))
        self._outround()
        url = reverse("outround_pairing_view", args=[0, 8])

        def read_page():
            caches["default"].clear()
            caches["filesystem"].clear()
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            # Ignore session/settings initialization writes; budget the data reads.
            reads = sum(query["sql"].lstrip().upper().startswith("SELECT")
                        for query in queries)
            return response, reads

        _, single_queries = read_page()
        for index in range(1, 4):
            self._outround(index)
        response, full_queries = read_page()
        self.assertEqual(single_queries, full_queries)
        self.assertEqual(len(response.context["outround_sections"][0]["outrounds"]), 4)
