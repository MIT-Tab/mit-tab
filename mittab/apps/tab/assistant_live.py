"""Allowlisted, read-only tournament data for the staff assistant."""

from django.db.models import Count, Q
from django.utils.timezone import now

from mittab.apps.tab.models import CheckIn, Judge, Outround, Round, TabSettings, Team


ALLOWED_SETTINGS = frozenset((
    "decimal_speaks", "min_speak", "max_speak", "min_eballot_speak",
    "max_eballot_speak", "tot_rounds", "cur_round",
))


def get_setting(key):
    if not isinstance(key, str) or key not in ALLOWED_SETTINGS:
        return {"error": "This setting is unavailable to the assistant."}
    return {"key": key, "value": TabSettings.get(key, None, use_cache=False)}


def find_roster(kind, name, round_number=None):
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 60:
        return {"error": "Enter at least two characters of a team or judge name."}
    if kind == "teams":
        teams = Team.objects.filter(name__icontains=name.strip()).order_by("name")
        return {"teams": list(teams.values("name", "checked_in")[:12])}
    if kind != "judges":
        return {"error": "Choose teams or judges."}
    judges = list(Judge.objects.filter(name__icontains=name.strip())
                  .order_by("name").values("id", "name")[:12])
    if round_number is None:
        return {"judges": [{"name": judge["name"]} for judge in judges]}
    if type(round_number) is not int or not 1 <= round_number <= 100:
        return {"error": "Specify a valid inround number."}
    checked_in = set(CheckIn.objects.filter(
        judge_id__in=[judge["id"] for judge in judges],
        round_number=round_number,
    ).values_list("judge_id", flat=True))
    return {
        "round_number": round_number,
        "judges": [
            {"name": judge["name"], "checked_in": judge["id"] in checked_in}
            for judge in judges
        ],
    }


def get_round(round_number, bracket="inround"):
    if type(round_number) is not int or not 1 <= round_number <= 100:
        return {"error": "Specify a valid round number or outround bracket size."}
    if bracket == "inround":
        rounds = Round.objects.filter(round_number=round_number)
    elif bracket in ("varsity", "novice"):
        rounds = Outround.objects.filter(
            num_teams=round_number,
            type_of_round=Outround.VARSITY if bracket == "varsity" else Outround.NOVICE,
        )
    else:
        return {"error": "Choose inround, varsity, or novice."}
    counts = rounds.aggregate(
        total=Count("id"),
        missing=Count("id", filter=Q(victor=Round.UNKNOWN)),
    )
    first_pairings = rounds.select_related("gov_team", "opp_team", "room", "chair")
    first_pairings = first_pairings.prefetch_related("judges").order_by("id")[:18]
    return {
        "round_number_or_bracket_size": round_number,
        "bracket": bracket,
        "total_pairings": counts["total"],
        "missing_outcome_count": counts["missing"],
        "missing_outcome_rooms": list(rounds.filter(victor=Round.UNKNOWN)
                                      .order_by("id").values_list("room__name", flat=True)[:18]),
        "pairings": [
            {
                "gov": pairing.gov_team.name,
                "opp": pairing.opp_team.name,
                "room": pairing.room.name if pairing.room else None,
                "chair": pairing.chair.name if pairing.chair else None,
                "judges": sorted(judge.name for judge in pairing.judges.all()),
                "outcome": pairing.get_victor_display(),
            }
            for pairing in first_pairings
        ],
        "pairings_truncated": counts["total"] > 18,
        "read_at": now().isoformat(),
    }
