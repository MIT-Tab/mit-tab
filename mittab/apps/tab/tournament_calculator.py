"""APDA points and reachable record bounds for the configured inround count.

Reference: https://apda.online/calc/ (including calc.php), checked 2026-11-13.
Zero teams explicitly means zero points rather than APDA Calc's minimum value.
"""

from functools import lru_cache


def tournament_value(team_count):
    if not team_count:
        return 0
    if team_count < 16:
        return 8
    return min(20, 10 + team_count // 8)


def next_record_distributions(brackets):
    """Enumerate both winners of each pullup, pairing from the top down.

    Each tuple is ordered from undefeated to winless. An unmatched team meets
    a team from the next nonempty bracket; the final unmatched team gets a bye.
    Within-bracket outcomes cannot affect bracket sizes.
    """
    outcomes = {(tuple([0] * (len(brackets) + 1)), None)}
    for losses, count in enumerate(brackets):
        next_outcomes = set()
        for counts, pending in outcomes:
            if pending is not None and count:
                remainder = count - 1
                for higher_wins in (True, False):
                    row = list(counts)
                    row[pending + int(not higher_wins)] += 1
                    row[losses + int(higher_wins)] += 1
                    row[losses] += remainder // 2
                    row[losses + 1] += remainder // 2
                    next_outcomes.add((tuple(row), losses if remainder % 2 else None))
            else:
                row = list(counts)
                row[losses] += count // 2
                row[losses + 1] += count // 2
                next_outcomes.add((tuple(row), losses if count % 2 else pending))
        outcomes = next_outcomes
    distributions = set()
    for counts, pending in outcomes:
        row = list(counts)
        if pending is not None:
            row[pending] += 1
        distributions.add(tuple(row))
    return distributions


@lru_cache(maxsize=64)
def record_bounds(team_count, num_rounds):
    """Find reachable min/max down-0/down-1 totals and a whole path to each.

    Deduplicate identical bracket distributions after each round: their future
    possibilities are identical regardless of which results led to them.
    Each displayed table follows one consistent path to a final-round bound.
    """
    paths = {(team_count,): ()}
    for _ in range(num_rounds):
        next_paths = {}
        for state, path in sorted(paths.items()):
            for following in sorted(next_record_distributions(state)):
                next_paths.setdefault(following, path + (following,))
        paths = next_paths
    order = lambda state: (sum(state[:2]), state)
    return paths[min(paths, key=order)], paths[max(paths, key=order)]


def calculator_context(team_count, num_rounds, case="best"):
    value = tournament_value(team_count)
    awards = [
        ("Winner", value),
        ("Finals", value - 4),
        ("Semis", (value - 8) * 0.75),
        ("Quarters", (value - 12) * 0.5),
        ("Octos", (value - 18) * 0.75),
    ]
    best, worst = record_bounds(team_count, num_rounds)
    path = worst if case == "worst" else best
    rounds = [
        [
            {"record": f"{number - losses}-{losses}", "count": count}
            for losses, count in enumerate(brackets)
        ]
        for number, brackets in enumerate(path, start=1)
    ]
    return {
        "team_count": team_count,
        "value": value,
        "toty": [(label, points) for label, points in awards if points > 0],
        "soty": [
            (place + 1, value - place * 2.5)
            for place in range(8)
            if value - place * 2.5 > 0
        ],
        "round_numbers": list(range(num_rounds, 0, -1)),
        "top_bracket_count": (
            sum(cell["count"] for cell in rounds[-1][:2]) if rounds else team_count
        ),
        "record_rows": [
            [column[row] if row < len(column) else None for column in reversed(rounds)]
            for row in range(num_rounds + 1)
        ],
    }
