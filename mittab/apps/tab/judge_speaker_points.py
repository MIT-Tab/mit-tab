"""Summarize how judges' recorded speaker points compare across inrounds."""

from collections import Counter, defaultdict
from itertools import product
from math import ceil, floor, log10
from statistics import mean

from mittab.apps.tab.models import Round, RoundStats

PLOT_LEFT = 56
PLOT_TOP, PLOT_BOTTOM = 18, 248


def judge_speaker_metrics(minimum_rounds):
    """Return ranked metrics using saved inround scores shared by the whole panel."""
    played_rounds = Round.objects.filter(victor__in=(Round.GOV, Round.OPP))
    scores = list(RoundStats.objects.filter(round__in=played_rounds).values_list(
        "round_id", "debater_id", "speaks",
    ))
    if not scores:
        return []

    round_scores = defaultdict(list)
    debater_totals, debater_counts = Counter(), Counter()
    round_totals, round_counts = Counter(), Counter()
    for round_id, debater_id, speaks in scores:
        round_scores[round_id].append(speaks)
        debater_totals[debater_id] += speaks
        debater_counts[debater_id] += 1
        round_totals[round_id, debater_id] += speaks
        round_counts[round_id, debater_id] += 1

    # Keep a single contribution per saved score, even on panels. All speeches
    # by an ironman in this debate are excluded from their comparison average.
    round_gaps = defaultdict(list)
    for round_id, debater_id, speaks in scores:
        key = (round_id, debater_id)
        other_count = debater_counts[debater_id] - round_counts[key]
        if other_count:
            other_mean = (debater_totals[debater_id] - round_totals[key]) / other_count
            round_gaps[round_id].append(speaks - other_mean)

    judge_rounds = defaultdict(set)
    judge_names = {}
    assignments = played_rounds.filter(judges__isnull=False).values_list(
        "pk", "judges__pk", "judges__name",
    )
    for round_id, judge_id, judge_name in assignments:
        if round_id in round_scores:
            judge_rounds[judge_id].add(round_id)
            judge_names[judge_id] = judge_name

    metrics = []
    for judge_id, rounds in judge_rounds.items():
        if len(rounds) < minimum_rounds:
            continue
        speaks = [score for round_id in rounds for score in round_scores[round_id]]
        gaps = [gap for round_id in rounds for gap in round_gaps[round_id]]
        metrics.append({
            "id": judge_id,
            "name": judge_names[judge_id],
            "rounds": len(rounds),
            "mean": round(mean(speaks), 2),
            "generosity": round(mean(gaps), 2) if gaps else None,
        })

    metrics.sort(key=lambda row: (-row["mean"], row["name"].casefold(), row["id"]))
    _assign_ranks(metrics, "mean")
    adjusted = [row for row in metrics if row["generosity"] is not None]
    adjusted.sort(key=lambda row: row["generosity"], reverse=True)
    _assign_ranks(adjusted, "generosity")
    return metrics


def _assign_ranks(rows, metric):
    ranks = {}
    for position, row in enumerate(rows, 1):
        row[f"{metric}_rank"] = ranks.setdefault(row[metric], position)


def _axis(values, start, end):
    low, high = float(min(values)), float(max(values))
    padding = max((high - low) * 0.1, 0.04)
    low, high = low - padding, high + padding
    ideal_step = (high - low) / 4
    unit = 10 ** floor(log10(ideal_step))
    step = next(unit * n for n in (1, 2, 5, 10) if unit * n >= ideal_step)
    first, last = floor(low / step), ceil(high / step)

    def position(value):
        fraction = (float(value) / step - first) / (last - first)
        return round(start + fraction * (end - start), 1)

    ticks = [
        {
            "position": position(i * step),
            "text": f"{i * step:.2f}".rstrip("0").rstrip("."),
            "zero": i == 0,
        }
        for i in range(first, last + 1)
    ]
    return position, ticks


def _overlap(box, other):
    left, top, right, bottom = box
    return left < other[2] and top < other[3] and right > other[0] and bottom > other[1]


def _place_labels(points, outliers, right):
    labels = []
    occupied = [(p["x"] - 7, p["y"] - 7, p["x"] + 7, p["y"] + 7) for p in points]
    candidates = [p for p in points if p["highlight"] or p["id"] in outliers]
    candidates.sort(key=lambda p: not p["highlight"])
    for point in candidates:
        x, y = point["x"], point["y"]
        name = point["name"]
        label = name if len(name) <= 26 else name[:25] + "…"
        width = len(label) * 8 + 6
        preferred_side = 1 if x < (PLOT_LEFT + right) / 2 else -1
        offsets = (0, -18, 18, -36, 36, -54, 54, -72, 72)
        sides = (preferred_side, -preferred_side)
        for distance, offset, side in product((12, 32, 52), offsets, sides):
            label_x, label_y = x + side * distance, y + offset
            left = label_x if side == 1 else label_x - width
            box = (left, label_y - 9, left + width, label_y + 9)
            if (box[0] >= PLOT_LEFT and box[2] <= right
                    and box[1] >= PLOT_TOP and box[3] <= PLOT_BOTTOM
                    and not any(_overlap(box, other) for other in occupied)):
                occupied.append(box)
                labels.append({
                    "x": label_x, "y": label_y, "point": point, "text": label,
                    "anchor": "start" if side == 1 else "end",
                })
                break
    return labels


def speaker_points_plot(metrics, width):
    """Build the scatter plot coordinates and label the extremes when space permits."""
    comparable = [row for row in metrics if row["generosity"] is not None]
    if not comparable:
        return None
    generosity_order = sorted(
        comparable, key=lambda row: row["generosity"], reverse=True,
    )
    outliers = {
        row["id"]
        for order in (comparable, generosity_order)
        for row in order[:3] + order[-3:]
    }
    right = width - 18
    x_position, x_ticks = _axis([row["mean"] for row in comparable], PLOT_LEFT, right)
    y_position, y_ticks = _axis([0, *(row["generosity"] for row in comparable)],
                                PLOT_BOTTOM, PLOT_TOP)
    points = [
        {**row, "x": x_position(row["mean"]), "y": y_position(row["generosity"])}
        for row in comparable
    ]
    # Draw highlighted dots above the rest, including when panelists coincide.
    points.sort(key=lambda point: point["highlight"])
    return {
        "width": width,
        "right": right,
        "field_width": right - PLOT_LEFT,
        "x_label": (PLOT_LEFT + right) // 2,
        "points": points,
        "labels": _place_labels(points, outliers, right),
        "x_ticks": x_ticks,
        "y_ticks": y_ticks,
    }
