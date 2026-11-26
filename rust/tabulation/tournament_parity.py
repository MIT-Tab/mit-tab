"""Replay existing tournament fixtures against Django's complete rankings."""

from collections import defaultdict
from itertools import chain, pairwise
from pathlib import Path
import json
import struct
from types import SimpleNamespace
from unittest.mock import patch

from django.test.utils import override_settings

from mittab.apps.tab.models import Round, TabSettings
from mittab.libs.tab_logic import rankings

FIXTURES = Path(__file__).resolve().parents[2] / "mittab/apps/tab/fixtures"
STATS = rankings.TeamScore.stat_priority


class Row(SimpleNamespace):
    # Round identity matters when duplicate ballots are grouped into debates.
    __hash__ = object.__hash__
    __eq__ = object.__eq__
    __repr__ = object.__repr__


class Relation(list):
    def all(self):
        return list(self)


def fixture_rows(name):
    rows = defaultdict(dict)
    for entry in json.loads((FIXTURES / name).read_text()):
        fields = entry["fields"]
        rows[entry["model"]][entry["pk"]] = fields
    return rows


def replay_data(rows):
    rounds, teams, speakers = {}, {}, {}
    for team_id, fields in sorted(rows["tab.team"].items()):
        teams[team_id] = Row(
            id=team_id, tiebreaker=fields["tiebreaker"], gov_team=Relation(),
            opp_team=Relation(), byes=Relation(), no_shows=Relation(),
            debaters=Relation(),
        )
    for round_id, fields in sorted(rows["tab.round"].items()):
        gov, opp = teams[fields["gov_team"]], teams[fields["opp_team"]]
        round_obj = Row(
            id=round_id, round_number=fields["round_number"], victor=fields["victor"],
            gov_team=gov, opp_team=opp, gov_team_id=gov.id, opp_team_id=opp.id,
        )
        rounds[round_id] = round_obj
        gov.gov_team.append(round_obj)
        opp.opp_team.append(round_obj)
    for fields in rows["tab.bye"].values():
        teams[fields["bye_team"]].byes.append(Row(round_number=fields["round_number"]))
    for fields in rows["tab.noshow"].values():
        teams[fields["no_show_team"]].no_shows.append(Row(round_number=fields["round_number"]))
    for speaker_id, fields in sorted(rows["tab.debater"].items(), key=lambda item: item[1]["name"]):
        speakers[speaker_id] = Row(id=speaker_id, tiebreaker=fields["tiebreaker"],
                                   roundstats_set=Relation(), team=lambda: None)
    for team_id, team in teams.items():
        member_ids = rows["tab.team"][team_id]["debaters"]
        for speaker_id, speaker in speakers.items():
            if speaker_id in member_ids:
                team.debaters.append(speaker)
                if speaker.team() is None:
                    speaker.team = lambda team=team: team
    for _, fields in sorted(rows["tab.roundstats"].items()):
        speakers[fields["debater"]].roundstats_set.append(
            Row(round=rounds[fields["round"]], speaks=float(fields["speaks"]),
                ranks=float(fields["ranks"])),
        )
    return rounds, teams, speakers


def optional(value):
    return "-" if value is None else value


def round_field(round_obj):
    return (round_obj.id, round_obj.round_number, round_obj.gov_team_id,
            round_obj.opp_team_id, round_obj.victor)


def serialize(rounds, teams, speakers):
    round_indexes = {pk: index for index, pk in enumerate(rounds)}
    team_indexes = {pk: index for index, pk in enumerate(teams)}
    speaker_indexes = {pk: index for index, pk in enumerate(speakers)}
    yield len(rounds)
    yield from chain.from_iterable(map(round_field, rounds.values()))
    yield len(teams)
    for team in teams.values():
        yield team.id
        yield optional(team.tiebreaker)
        for entries in (team.byes, team.no_shows):
            yield len(entries)
            yield from (entry.round_number for entry in entries)
        yield len(team.debaters)
        yield from (speaker_indexes[speaker.id] for speaker in team.debaters)
    yield len(speakers)
    for speaker in speakers.values():
        yield speaker.id
        yield optional(speaker.tiebreaker)
        team = speaker.team()
        yield optional(None if team is None else team_indexes[team.id])
        yield len(speaker.roundstats_set)
        for ballot in speaker.roundstats_set:
            yield from (round_indexes[ballot.round.id], ballot.speaks, ballot.ranks)


def expected_standings(scores):
    ordered = sorted(scores)
    tiebreakers = [first.get_tiebreaker(second) for first, second in pairwise(ordered)]
    return ",".join(
        ":".join(map(str, (
            getattr(score, "team" if isinstance(score, rankings.TeamScore) else "debater").id,
            optional(None if stat is None else STATS.index(stat)),
            *(struct.pack(">d", score.stats.get(metric) or 0).hex() for metric in STATS),
        )))
        for score, stat in zip(ordered, [*tiebreakers, None])
    )


def add_legacy_outcomes(rows):
    # Exercise all result states, multiple ballots in a debate, and ballots
    # assigned to a different debate in the same round.
    rounds = rows["tab.round"]
    speakers = rows["tab.roundstats"]
    first_id, second_id = [pk for pk, fields in rounds.items() if fields["round_number"] == 1][:2]
    original = next(fields for fields in speakers.values() if fields["round"] == first_id)
    extra_id = max(speakers) + 1
    speakers[extra_id] = {**original, "speaks": "29.375", "ranks": "2.5"}
    speakers[extra_id + 1] = {**original, "round": second_id, "speaks": "28.875", "ranks": "1.5"}
    rounds[second_id]["opp_team"] = rounds[second_id]["gov_team"]
    rounds[second_id]["victor"] = Round.ALL_WIN
    for index, fields in enumerate(rounds.values()):
        if index % 5 == 0:
            fields["victor"] = index % (Round.ALL_WIN + 1)


def tournament_cases():
    cache = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
    fixtures = (
        ("testing_empty.json", False),
        ("testing_db.json", False),
        ("testing_finished_db.json", False),
        ("testing_finished_db.json", True),
    )
    for name, legacy in fixtures:
        rows = fixture_rows(name)
        if legacy:
            add_legacy_outcomes(rows)
        _, teams, speakers = tournament = replay_data(rows)
        data = " ".join(map(str, serialize(*tournament)))
        scenarios = ((1, 0), (6, 0), (6, 3)) if rows["tab.round"] else ((1, 0), (2, 1))
        for current_round, lenient_late in scenarios:
            for team in teams.values():
                for no_show in team.no_shows:
                    no_show.lenient_late = no_show.round_number <= lenient_late
            with override_settings(CACHES=cache), patch.object(
                TabSettings, "get", side_effect={"cur_round": current_round}.__getitem__
            ):
                for exclude, through in ((None, None), (1, 3), (4, None)):
                    scores = [rankings.TeamScore(team, exclude, through) for team in teams.values()]
                    yield emit(data, 0, current_round, lenient_late, exclude, through, scores)
                for mode in (1, 2):
                    priority = rankings.speaker_stat_priority(mode == 2)
                    scores = [rankings.DebaterScore(speaker, priority) for speaker in speakers.values()]
                    yield emit(data, mode, current_round, lenient_late, None, None, scores)


def emit(data, mode, current_round, lenient_late, exclude, through, scores):
    fields = (mode, current_round, lenient_late, optional(exclude), optional(through), data)
    return "tournament " + " ".join(map(str, fields)), expected_standings(scores)
