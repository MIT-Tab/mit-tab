"""Exercise the unmodified Django round planner with in-memory tournament rows."""

from contextlib import redirect_stdout
from io import StringIO
from random import Random
from types import SimpleNamespace
from unittest.mock import patch

from mittab.libs import errors, tab_logic


class Participants(list):
    def exclude(self, pk__in):
        return Participants(team for team in self if team.id not in pk__in)


class Team(SimpleNamespace):
    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, Team) and self.id == other.id


class Manager:
    def __init__(self):
        self.rows = []

    def bulk_create(self, objects):
        self.rows.extend(objects)


def reference(teams, settings):
    class Round(SimpleNamespace):
        NONE, GOV, OPP = 0, 1, 2
        pullup = NONE

    class Bye(SimpleNamespace):
        def save(self):
            self.objects.rows.append(self)

    Round.objects = Manager()
    Round.objects.exclude = lambda **kwargs: [
        SimpleNamespace(gov_team=team, pullup=Round.GOV) for team in teams if team.pulled_up
    ]
    Bye.objects = Manager()
    TeamModel = SimpleNamespace(
        FREE_SEED=1, UNSEEDED=0,
        objects=SimpleNamespace(filter=lambda **kwargs: []),
        with_preloaded_relations_for_tabbing=lambda: SimpleNamespace(
            filter=lambda **kwargs: Participants(teams)
        ),
    )

    def ranking(group):
        return sorted(group, key=lambda team: (-team.wins, -team.speaks, team.coin))

    with patch.multiple(
        tab_logic,
        Team=TeamModel, Round=Round, Bye=Bye, validate_round_data=lambda _: None,
        rank_teams_except_record=ranking,
        rank_teams=lambda: [SimpleNamespace(team=team) for team in ranking(teams)],
        num_byes=lambda team: team.prior_byes,
        num_forfeit_wins=lambda team: team.average - team.prior_byes,
        had_bye=lambda team: team.prior_byes != 0,
        num_govs=lambda team: team.gov,
        num_opps=lambda team: team.opp,
        hit_before=lambda left, right: right.id in left.opponents,
        hit_pull_up=lambda team: team.hit_pull_up,
        tot_wins=lambda team: team.wins,
    ), patch.object(tab_logic.TabSettings, "get", side_effect=settings.get), patch.object(
        tab_logic.TabSettings, "set"
    ), redirect_stdout(StringIO()):
        try:
            tab_logic.pair_round()
        except (errors.NotEnoughTeamsError, IndexError, StopIteration):
            return "error"
        except ValueError as error:
            if str(error) != "list.remove(x): x not in list":
                raise
            # The old planner can try to move an average-score team twice
            # after it has already been pulled into the higher bracket.
            return None
    byes = ",".join(str(row.bye_team.id) for row in Bye.objects.rows)
    rounds = ",".join(
        f"{row.gov_team.id}:{row.opp_team.id}:"
        f"{row.gov_team.id if row.pullup == Round.GOV else row.opp_team.id if row.pullup == Round.OPP else '-'}"
        for row in Round.objects.rows
    )
    return f"{byes}|{rounds}"


def serialize(settings, teams):
    fields = [
        "prelim", settings["cur_round"], settings["tot_rounds"],
        settings["min_byes"], settings["fair_bye"],
        settings["r2_random_within_brackets"], len(teams),
    ]
    for team in teams:
        school = "-" if team.school_id is None else team.school_id
        fields.extend((
            team.id, team.seed, team.gov, team.opp, school, team.wins,
            int(team.hit_pull_up), team.prior_byes, team.average, int(team.pulled_up),
            team.speaks, team.coin, len(team.opponents), *team.opponents,
        ))
    return " ".join(map(str, fields))


def prelim_cases():
    rng = Random(67190)
    for seed in (0, 1, 0xBEEF, 1337, 2**32 - 1, 2**32, 2**64 - 1):
        reference_rng = Random(seed)
        bounds = [rng.randrange(2**64) for _ in range(1_000)]
        bounds.extend((0, 1, 2, 4, 65_537, 2**32 - 1, 2**32, 2**64 - 1))
        yield (
            " ".join(map(str, ("legacy-random", seed, *bounds))),
            ",".join(str(reference_rng._randbelow(bound)) for bound in bounds),
        )
    for round_number in range(1, 7):
        for index in range(125):
            size = rng.randint(1, 40)
            minimum = rng.randint(1, min(size, 5)) if index % 3 == 0 else 0
            settings = {
                "cur_round": round_number, "tot_rounds": 5, "min_byes": minimum,
                "fair_bye": index % 3, "r2_random_within_brackets": int(index % 2 == 0),
            }
            ids = sorted(rng.sample(range(1, 800), size))
            rank_values = rng.sample(range(1, 10000), size)
            teams = []
            for team_id, coin in zip(ids, rank_values, strict=True):
                wins = rng.randrange(round_number)
                prior_byes = min(wins, rng.choice((0, 0, 1, 2)))
                average = rng.randrange(prior_byes, round_number)
                gov = rng.randrange(round_number)
                teams.append(Team(
                    id=team_id, pk=team_id, coin=coin,
                    wins=wins, prior_byes=prior_byes, average=average,
                    gov=gov, opp=round_number - 1 - gov,
                    seed=rng.randrange(4), school_id=rng.choice((None, 1, 2, 3)),
                    hit_pull_up=bool(rng.randrange(3) == 0),
                    pulled_up=bool(rng.randrange(3) == 0),
                    speaks=rng.randrange(5) * 2.5,
                    opponents=set(),
                    byes=SimpleNamespace(count=lambda n=prior_byes: n),
                    no_shows=SimpleNamespace(all=lambda: ()),
                ))
            if round_number > 1:
                for i, team in enumerate(teams):
                    for opponent in teams[:i]:
                        if rng.randrange(14) == 0:
                            team.opponents.add(opponent.id)
                            opponent.opponents.add(team.id)
            expected = reference(teams, settings)
            if expected is not None:
                yield serialize(settings, teams), expected
