"""Compare the Rust tabulation library with the current Django rules."""

from itertools import product
from pathlib import Path
from random import Random
from types import SimpleNamespace
from unittest.mock import patch
import subprocess

import django
from django.test.utils import override_settings

django.setup()

from mittab.apps.tab.models import TabSettings
from mittab.apps.tab.tournament_calculator import calculator_context
from mittab.libs import mwmatching, tab_logic
from mittab.libs.errors import ByeAssignmentError
from mittab.libs.outround_tab_logic.bracket_generation import gen_bracket
from mittab.libs.tab_logic import rankings, stats
from .tournament_parity import tournament_cases


def bracket_cases():
    for size in (2**power for power in range(1, 14)):
        expected = ",".join(f"{left}:{right}" for left, right in gen_bracket(size))
        yield f"bracket {size}", expected


def bye_cases():
    for count in range(65):
        for minimum in range(-1, count + 2):
            with patch.object(TabSettings, "get", return_value=minimum):
                try:
                    expected = str(tab_logic.required_bye_count(count))
                except ByeAssignmentError as error:
                    expected = {
                        "Min byes must be nonnegative.": "negative",
                        "Min byes cannot exceed the number of checked-in teams.": "insufficient",
                    }[str(error)]
            yield f"bye-count {count} {minimum}", expected

    for policy in (0, 1, 2, 3):
        with patch.object(TabSettings, "get", return_value=policy):
            for size in range(1, 6):
                for seeds in product(range(4), repeat=size):
                    teams = [SimpleNamespace(index=index, seed=seed)
                             for index, seed in enumerate(seeds)]
                    expected = tab_logic.first_round_bye_candidates(teams)
                    yield case("bye-candidates", policy, *seeds), joined(
                        team.index for team in expected
                    )


def side_cases():
    with patch.multiple(tab_logic, num_govs=lambda team: team.gov,
                        num_opps=lambda team: team.opp):
        for first_gov, first_opp, second_gov, second_opp in product(range(8), repeat=4):
            first = SimpleNamespace(gov=first_gov, opp=first_opp)
            second = SimpleNamespace(gov=second_gov, opp=second_opp)
            with patch.object(tab_logic.random, "randint", return_value=0) as coin:
                result = tab_logic.determine_gov_opp([(first, second)])[0][0]
                if coin.called:
                    expected = "coin"
                else:
                    expected = "0" if result is first else "1"
            yield case("government", first_gov, first_opp, second_gov, second_opp), expected


WEIGHT_NAMES = (
    "power_pairing_multiple",
    "high_opp_penalty",
    "high_high_opp_penalty",
    "high_gov_penalty",
    "same_school_penalty",
    "hit_pull_up_before",
    "hit_team_before",
)


def weight_cases():
    rng = Random(61754)
    with patch.multiple(tab_logic, num_govs=lambda team: team.gov,
                        num_opps=lambda team: team.opp, tot_wins=lambda team: team.wins,
                        hit_pull_up=lambda team: team.pull_up):
        for index in range(5000):
            first = random_team(rng)
            second = random_team(rng)
            current_round = rng.randint(1, 5)
            total_rounds = rng.randint(0, 9)
            rematch = rng.choice([True, False])

            if index < 20:
                # Integer settings and seeds are stored in signed 32-bit DB columns.
                seeds = [-(2**31), 2**31 - 1, 2**31 - 1, 2**31 - 1]
                positions = [0, 2**31, 2**31 - 1, 2**32 - 1]
                values = [rng.randint(-(2**31), 2**31 - 1) for _ in WEIGHT_NAMES]
            else:
                seeds = [rng.randint(0, 3) for _ in range(4)]
                positions = [rng.randint(0, 255) for _ in range(4)]
                values = [rng.randint(-100000, 100000) for _ in WEIGHT_NAMES]
            first.seed, second.seed, first_ideal_seed, second_ideal_seed = seeds
            first_ideal = SimpleNamespace(seed=first_ideal_seed)
            second_ideal = SimpleNamespace(seed=second_ideal_seed)
            first_pos, second_pos, first_ideal_pos, second_ideal_pos = positions

            with patch.object(tab_logic, "hit_before", return_value=rematch):
                expected = tab_logic.calc_weight(
                    first, second, first_pos, second_pos, first_ideal, second_ideal,
                    first_ideal_pos, second_ideal_pos, dict(zip(WEIGHT_NAMES, values, strict=True)),
                    current_round, total_rounds,
                )
            yield case(
                "weight", current_round, total_rounds, int(rematch), *team_fields(first),
                *team_fields(second), *(seeds if current_round == 1 else positions), *values,
            ), expected


def random_team(rng):
    return SimpleNamespace(
        gov=rng.randint(0, 6),
        opp=rng.randint(0, 6),
        school_id=rng.choice([None, 1, 2]),
        wins=rng.randint(0, 8),
        pull_up=rng.choice([True, False]),
    )


def team_fields(team):
    return (team.gov, team.opp, "-" if team.school_id is None else team.school_id,
            team.wins, int(team.pull_up))


STATS = rankings.TeamScore.stat_priority


def standings_cases():
    rng = Random(47095)
    for mode, priority in enumerate((
        STATS, rankings.speaker_stat_priority(False), rankings.speaker_stat_priority(True),
    )):
        # Include exact ties and score sets tied through every individual criterion.
        zero = [None] * len(STATS)
        score_sets = [[], [zero], [zero, zero]]
        for stat in priority:
            different = zero.copy()
            different[STATS.index(stat)] = 1
            score_sets.append([zero, different, zero])
        for _ in range(250):
            base = [rng.randint(0, 20) / 3 for _ in STATS]
            score_sets.append([
                [rng.choice([value, value, value, value + 0.01, None]) for value in base]
                for _ in range(rng.randint(2, 40))
            ])

        for scores in score_sets:
            python_scores = []
            for values in scores:
                score = rankings.Score()
                score.stat_priority = priority
                score.stats = dict(zip(STATS, values, strict=True))
                python_scores.append(score)
            order = sorted(range(len(scores)), key=python_scores.__getitem__)
            tiebreakers = []
            for left, right in zip(order, order[1:]):
                stat = python_scores[left].get_tiebreaker(python_scores[right])
                tiebreakers.append("-" if stat is None else STATS.index(stat))
            yield case(
                "standings", mode, len(scores),
                *(value if value is not None else 0 for row in scores for value in row),
            ), f"{joined(order)}|{joined(tiebreakers)}"


def totals_cases():
    rng = Random(19284)
    cache = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
    with override_settings(CACHES=cache), patch.multiple(
        stats,
        speaks_for_debater=lambda debater, *_args, **_kwargs: list(debater.speaks),
        ranks_for_debater=lambda debater, *_args, **_kwargs: list(debater.ranks),
    ):
        for count in range(4):
            for sample in range(125):
                debaters = []
                for _ in range(count):
                    length = sample if sample < 7 else rng.randint(0, 15)
                    debaters.append(SimpleNamespace(
                        speaks=[rng.randint(0, 3000) / 100 for _ in range(length)],
                        ranks=[rng.randint(1, 8) / 2 for _ in range(length)],
                    ))

                team = SimpleNamespace(debaters=SimpleNamespace(all=lambda: debaters))
                for metric, team_functions, speaker_functions in (
                    ("speaks",
                     (stats.tot_speaks, stats.single_adjusted_speaks, stats.double_adjusted_speaks),
                     (stats.tot_speaks_deb, stats.single_adjusted_speaks_deb,
                      stats.double_adjusted_speaks_deb)),
                    ("ranks",
                     (stats.tot_ranks, stats.single_adjusted_ranks, stats.double_adjusted_ranks),
                     (stats.tot_ranks_deb, stats.single_adjusted_ranks_deb,
                      stats.double_adjusted_ranks_deb)),
                ):
                    rows = [getattr(debater, metric) for debater in debaters]
                    expected = tuple(function(team) for function in team_functions)
                    fields = [value for row in rows for value in (len(row), *row)]
                    yield case("scores", len(rows), *fields), expected

                    if count == 1:
                        expected = tuple(function(debaters[0]) for function in speaker_functions)
                        yield case("scores", 1, *fields), expected


def matching_cases():
    rng = Random(74732)
    graphs = [(0, []), (6, []), (6, [(1, 4, -8)])]
    for vertex_count in range(2, 25):
        for sample in range(75):
            graph = []
            for first in range(vertex_count):
                for second in range(first):
                    if rng.randrange(16) >= 1 + sample % 16:
                        continue
                    if sample % 3 == 0:
                        weight = rng.randint(-2, 2)
                    elif sample % 3 == 1:
                        weight = rng.randint(-100, 100) / 10
                    else:
                        weight = rng.randint(-600, 1200) / 2
                    if rng.choice((False, True)):
                        graph.append((first, second, weight))
                    else:
                        graph.append((second, first, weight))
            rng.shuffle(graph)
            graphs.append((vertex_count, graph))
    # Include larger graphs shaped like team pairing and judge assignment.
    for count in (40, 60, 96, 150):
        for bipartite in (False, True):
            graph = [
                (first, second, rng.randint(-2000, 10) / 4)
                for first in range(count)
                for second in range(first)
                if not bipartite or second < count // 2 <= first
            ]
            graphs.append((count, graph))
    for count, edges in graphs:
        for max_cardinality in (False, True):
            result = mwmatching.maxWeightMatching(edges, max_cardinality)
            result.extend([-1] * (count - len(result)))
            payload = (value for edge in edges for value in edge)
            yield case("matching", count, int(max_cardinality), *payload), joined(result)


def calculator_cases():
    for teams, rounds in product(range(257), range(13)):
        expected = calculator_context(teams, rounds)
        toty = joined(f"{name}:{value:g}" for name, value in expected["toty"])
        soty = joined(f"{place}:{value:g}" for place, value in expected["soty"])
        columns = [
            joined(row[column]["count"] for row in expected["record_rows"] if row[column])
            for column in range(rounds - 1, -1, -1)
        ]
        yield case("calculator", teams, rounds), "|".join(
            (str(expected["value"]), toty, soty, ";".join(columns))
        )


def case(*fields):
    return " ".join(map(str, fields))


def joined(fields):
    return ",".join(map(str, fields))


def main():
    manifest = Path(__file__).with_name("Cargo.toml")
    tests = [
        *bracket_cases(), *bye_cases(), *side_cases(), *weight_cases(),
        *standings_cases(), *totals_cases(), *tournament_cases(),
        *matching_cases(), *calculator_cases(),
    ]
    commands = [command for command, _ in tests]
    output = subprocess.run(
        ["cargo", "run", "--quiet", "--locked", "--manifest-path", str(manifest),
         "--example", "parity"],
        input="\n".join(commands) + "\n", text=True, check=True, stdout=subprocess.PIPE,
    ).stdout.splitlines()
    for (command, expected), actual in zip(tests, output, strict=True):
        if isinstance(expected, float):
            actual = float(actual)
        elif isinstance(expected, tuple):
            actual = tuple(map(float, actual.split(",")))
        if actual != expected:
            if command.startswith("tournament "):
                description = " ".join(command.split(maxsplit=6)[:6])
                actual_rows = actual.split(",") if actual else []
                expected_rows = expected.split(",") if expected else []
                if len(actual_rows) != len(expected_rows):
                    raise AssertionError(
                        f"{description}: Django returned {len(expected_rows)} rows, "
                        f"Rust returned {len(actual_rows)} rows"
                    )
                for position, (python_row, rust_row) in enumerate(
                    zip(expected_rows, actual_rows), start=1
                ):
                    if python_row != rust_row:
                        raise AssertionError(
                            f"{description}, place {position}\n"
                            f"Django: {python_row}\nRust: {rust_row}"
                        )
            raise AssertionError(f"{command}\nDjango: {expected}\nRust: {actual}")
    print(f"{len(tests):,} Django/Rust comparisons passed.")


if __name__ == "__main__":
    main()
