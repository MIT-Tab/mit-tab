from decimal import Decimal

from django.test import TestCase
from django.db.models import Q
import pytest

from mittab.apps.tab.models import (
    BreakingTeam,
    CheckIn,
    Judge,
    Outround,
    Room,
    RoomCheckIn,
    School,
    TabSettings,
    Team,
)
from mittab.libs import assign_judges, assign_rooms


@pytest.mark.django_db
class TestOutroundAssignmentScope(TestCase):
    pytestmark = pytest.mark.django_db

    def setUp(self):
        super().setUp()
        self.school = School.objects.create(name="Scope School")
        self.var_round = self._create_varsity_round()
        self.nov_round = self._create_novice_round()
        self._create_judges()
        self._create_rooms()
        TabSettings.set("var_panel_size", 3)
        TabSettings.set("nov_panel_size", 3)
        TabSettings.set("outs_judge_pairing_mode", 0)  # snake draft
        TabSettings.set("outs_round_priority", 0)  # top of bracket

    def _create_team(self, name):
        return Team.objects.create(
            name=name,
            school=self.school,
            seed=Team.FULL_SEED,
        )

    def _create_varsity_round(self):
        teams = [self._create_team(f"Varsity Team {i}") for i in range(1, 5)]
        for i, team in enumerate(teams, start=1):
            BreakingTeam.objects.create(
                team=team,
                seed=i,
                effective_seed=i,
                type_of_team=BreakingTeam.VARSITY,
            )
        return [
            Outround.objects.create(
                num_teams=4,
                type_of_round=Outround.VARSITY,
                gov_team=teams[0],
                opp_team=teams[3],
            ),
            Outround.objects.create(
                num_teams=4,
                type_of_round=Outround.VARSITY,
                gov_team=teams[1],
                opp_team=teams[2],
            ),
        ]

    def _create_novice_round(self):
        team_a = self._create_team("Novice Team 1")
        team_b = self._create_team("Novice Team 2")
        BreakingTeam.objects.create(
            team=team_a,
            seed=1,
            effective_seed=1,
            type_of_team=BreakingTeam.NOVICE,
        )
        BreakingTeam.objects.create(
            team=team_b,
            seed=2,
            effective_seed=2,
            type_of_team=BreakingTeam.NOVICE,
        )
        return Outround.objects.create(
            num_teams=2,
            type_of_round=Outround.NOVICE,
            gov_team=team_a,
            opp_team=team_b,
        )

    def _create_judges(self):
        for rank in range(9, 0, -1):
            judge = Judge.objects.create(
                name=f"Judge {rank}",
                rank=Decimal(rank),
            )
            CheckIn.objects.create(judge=judge, round_number=0)

    def _create_rooms(self):
        for rank in range(5, 1, -1):
            room = Room.objects.create(name=f"Room {rank}", rank=Decimal(rank))
            RoomCheckIn.objects.create(room=room, round_number=0)

    def _assigned_ranks(self, rounds):
        ranks = []
        for outround in rounds:
            for judge in outround.judges.all():
                ranks.append(int(judge.rank))
        return sorted(ranks, reverse=True)

    def test_assign_outround_judges_varsity_wings_priority(self):
        TabSettings.set("outround_judge_priority", 0)  # Varsity Wings
        assign_judges.add_outround_judges(
            round_specs=[(Outround.VARSITY, 4), (Outround.NOVICE, 2)]
        )

        varsity_rounds = list(
            Outround.objects.filter(type_of_round=Outround.VARSITY, num_teams=4)
            .prefetch_related("judges")
        )
        novice_round = Outround.objects.get(type_of_round=Outround.NOVICE, num_teams=2)

        self.assertEqual(self._assigned_ranks(varsity_rounds), [9, 8, 7, 6, 5, 4])
        self.assertEqual(self._assigned_ranks([novice_round]), [3, 2, 1])

    def test_assign_outround_judges_novice_chairs_priority(self):
        TabSettings.set("outround_judge_priority", 1)  # Novice Chairs
        assign_judges.add_outround_judges(
            round_specs=[(Outround.VARSITY, 4), (Outround.NOVICE, 2)]
        )

        varsity_rounds = list(
            Outround.objects.filter(type_of_round=Outround.VARSITY, num_teams=4)
            .prefetch_related("judges")
        )
        novice_round = Outround.objects.get(type_of_round=Outround.NOVICE, num_teams=2)

        self.assertEqual(self._assigned_ranks(varsity_rounds), [9, 8, 6, 5, 3, 2])
        self.assertEqual(self._assigned_ranks([novice_round]), [7, 4, 1])

    def test_assign_outround_rooms_across_scope_has_no_overlap(self):
        assign_rooms.add_outround_rooms(
            round_specs=[(Outround.VARSITY, 4), (Outround.NOVICE, 2)]
        )

        scoped_rounds = list(
            Outround.objects.filter(
                Q(type_of_round=Outround.VARSITY, num_teams=4)
                | Q(type_of_round=Outround.NOVICE, num_teams=2)
            )
        )
        room_ids = [round_obj.room_id for round_obj in scoped_rounds]
        self.assertNotIn(None, room_ids)
        self.assertEqual(len(room_ids), len(set(room_ids)))

    def test_assign_outround_judges_ignores_decided_rounds(self):
        decided_round = self.var_round[0]
        pending_round = self.var_round[1]
        sentinel_judge = Judge.objects.get(name="Judge 9")

        decided_round.victor = Outround.GOV
        decided_round.chair = sentinel_judge
        decided_round.save()
        decided_round.judges.add(sentinel_judge)

        assign_judges.add_outround_judges(round_specs=[(Outround.VARSITY, 4)])

        decided_round.refresh_from_db()
        pending_round.refresh_from_db()
        self.assertEqual(decided_round.chair_id, sentinel_judge.id)
        self.assertTrue(decided_round.judges.filter(id=sentinel_judge.id).exists())
        self.assertIsNotNone(pending_round.chair_id)

    def test_assign_outround_rooms_ignores_decided_rounds(self):
        decided_round = self.var_round[0]
        pending_round = self.var_round[1]
        sentinel_room = Room.objects.get(name="Room 5")

        decided_round.victor = Outround.GOV
        decided_round.room = sentinel_room
        decided_round.save()
        pending_round.room = None
        pending_round.save()

        assign_rooms.add_outround_rooms(round_specs=[(Outround.VARSITY, 4)])

        decided_round.refresh_from_db()
        pending_round.refresh_from_db()
        self.assertEqual(decided_round.room_id, sentinel_room.id)
        self.assertIsNotNone(pending_round.room_id)
