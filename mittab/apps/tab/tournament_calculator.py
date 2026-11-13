"""APDA Calc estimates, using live team counts and configured inrounds.

Reference: https://apda.online/calc/ (including calc.php), checked 2026-11-13.
Zero teams explicitly means zero points rather than APDA Calc's minimum value.
"""


def tournament_value(team_count):
    if not team_count:
        return 0
    if team_count < 16:
        return 8
    return min(20, 10 + team_count // 8)


def calculator_context(team_count, num_rounds):
    value = tournament_value(team_count)
    awards = [
        ("Winner", value),
        ("Finals", value - 4),
        ("Semis", (value - 8) * 0.75),
        ("Quarters", (value - 12) * 0.5),
        ("Octos", (value - 18) * 0.75),
    ]
    brackets = [team_count]
    rounds = []
    for round_number in range(1, num_rounds + 1):
        next_brackets = [0] * (round_number + 1)
        # Alternate the extra winner/loser in odd brackets, from the top
        # down, matching APDA Calc's pullup/bye rounding.
        extra_win = True
        for losses, count in enumerate(brackets):
            wins = count // 2
            if count % 2:
                wins += int(extra_win)
                extra_win = not extra_win
            next_brackets[losses] += wins
            next_brackets[losses + 1] += count - wins
        brackets = next_brackets
        rounds.append(
            [
                {"record": f"{round_number - losses}-{losses}", "count": count}
                for losses, count in enumerate(brackets)
            ]
        )
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
        "record_rows": [
            [column[row] if row < len(column) else None for column in reversed(rounds)]
            for row in range(num_rounds + 1)
        ],
    }
