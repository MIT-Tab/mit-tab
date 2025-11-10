from decimal import Decimal
import json
from django.db.models import Prefetch

from mittab.apps.tab.helpers import redirect_and_flash_error
from mittab.apps.tab.models import Bye, RoundStats, Team, Round, TabSettings, Debater
from mittab.libs.tab_logic.stats import (
    tot_ranks,
    tot_ranks_deb,
    tot_speaks,
    tot_speaks_deb,
)

GOV = "G"
OPP = "O"
def round_stats_lookup(round_obj):
    lookup = getattr(round_obj, "round_stats_lookup_cache", None)
    if lookup is None:
        stats = getattr(round_obj, "round_stats_cache", None)
        if stats is None:
            cache = getattr(round_obj, "_prefetched_objects_cache", {})
            stats = cache.get("roundstats_set")
            if stats is None:
                stats = list(round_obj.roundstats_set.all())
            setattr(round_obj, "round_stats_cache", stats)
        lookup = {}
        for stat in stats:
            lookup.setdefault(stat.debater_id, []).append(stat)
        setattr(round_obj, "round_stats_lookup_cache", lookup)
    return lookup


def safe_name(obj):
    return getattr(obj, "name", None) if obj else None


def safe_school_name(team):
    school = getattr(team, "school", None)
    return safe_name(school)


def get_debater_status(debater):
    if not debater:
        return None
    try:
        return Debater.NOVICE_CHOICES[debater.novice_status][1]
    except (AttributeError, IndexError, TypeError):
        return None


def get_debater_status_short(debater):
    status = get_debater_status(debater)
    return status[0] if status else ""


def get_victor_label(victor_code, side):
    side = 0 if side == GOV else 1
    victor_map = {
        0 : ("", ""),
        1: ("W", "L"),
        2: ("L", "W"),
        3: ("WF", "LF"),
        4: ("LF", "WF"),
        5: ("AD", "AD"),
        6: ("AW", "AW"),
    }
    return victor_map[victor_code][side]


def get_dstats(round_obj, deb1, deb2, iron_man):
    if deb1 is None:
        return None, None
    dstat_lookup = round_stats_lookup(round_obj)
    dstat1 = list(dstat_lookup.get(deb1.pk, []))
    if iron_man or deb2 is None:
        dstat2 = []
    else:
        dstat2 = list(dstat_lookup.get(deb2.pk, []))
    blank_rs = RoundStats(debater=deb1, round=round_obj, speaks=0, ranks=0)
    while len(dstat1) + len(dstat2) < 2:
        # Something is wrong with our data, but we don't want to crash
        dstat1.append(blank_rs)
    if not dstat2 and not dstat1:
        return None, None
    if not dstat2:
        dstat1, dstat2 = dstat1[0], dstat1[1]
    elif not dstat1:
        dstat1, dstat2 = dstat2[0], dstat2[1]
    else:
        dstat1, dstat2 = dstat1[0], dstat2[0]
    return dstat1, dstat2


def json_get_round(round_obj, team, deb1, deb2, bye_lookup=None):
    chair = round_obj.chair
    chair_name = safe_name(chair)
    judges = list(round_obj.judges.all())
    wings = [
        safe_name(judge)
        for judge in judges
        if judge
        and (not chair or judge.pk != chair.pk)
        and safe_name(judge)
    ]
    json_round = {
        "round_number": round_obj.round_number,
        "round_id": round_obj.pk,
        "side": GOV if round_obj.gov_team == team else OPP,
        "result": get_victor_label(
            round_obj.victor, GOV if round_obj.gov_team == team else OPP
        ),
        "chair": chair_name,
        "wings": wings,
    }

    opponent = round_obj.opp_team if round_obj.gov_team == team else round_obj.gov_team
    if opponent:
        opponent_debaters = list(opponent.debaters.all())
        json_round["opponent"] = {
            "name": opponent.get_or_create_team_code(),
            "school": safe_school_name(opponent),
            "debater1": safe_name(opponent_debaters[0]) if opponent_debaters else None,
            "debater2": (
                safe_name(opponent_debaters[1])
                if len(opponent_debaters) > 1
                else None
            ),
        }

    stats_lookup = round_stats_lookup(round_obj)
    if deb1:
        json_round["debater1"] = [
            (stat.speaks, stat.ranks)
            for stat in stats_lookup.get(deb1.pk, [])
        ]
    else:
        json_round["debater1"] = []

    if deb2:
        json_round["debater2"] = [
            (stat.speaks, stat.ranks)
            for stat in stats_lookup.get(deb2.pk, [])
        ]
    else:
        json_round["debater2"] = []

    if bye_lookup is not None:
        json_round["bye_round"] = bye_lookup.get(team.pk)
    else:
        json_round["bye_round"] = (
            Bye.objects.filter(bye_team=team)
            .values_list("round_number", flat=True)
            .first()
        )

    return json_round


class JSONDecimalEncoder(json.JSONEncoder):
    def default(self, o):  # pylint: disable=E0202
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def get_all_json_data():
    all_tab_cards_data = {}
    bye_lookup = {}
    for bye_team_id, round_number in Bye.objects.values_list(
        "bye_team_id", "round_number"
    ):
        bye_lookup.setdefault(bye_team_id, round_number)

    round_queryset = (
        Round.objects.select_related(
            "gov_team__school",
            "opp_team__school",
            "chair",
        )
        .prefetch_related(
            "judges",
            "gov_team__debaters",
            "opp_team__debaters",
            Prefetch("roundstats_set", queryset=RoundStats.objects.select_related("round")),
        )
        .order_by("round_number", "pk")
    )

    debater_queryset = Debater.objects.prefetch_related(
        Prefetch(
            "roundstats_set",
            queryset=RoundStats.objects.select_related("round"),
        ),
        "team_set",
        "team_set__no_shows",
    )

    teams = (
        Team.objects.select_related("school")
        .prefetch_related(
            Prefetch("debaters", queryset=debater_queryset),
            Prefetch("gov_team", queryset=round_queryset),
            Prefetch("opp_team", queryset=round_queryset),
            "no_shows",
            "byes",
        )
        .order_by("pk")
    )

    for team in teams:
        tab_card_data = {
            "team_name": team.get_or_create_team_code(),
            "team_school": safe_school_name(team),
        }

        debaters = list(team.debaters.all())
        deb1 = debaters[0] if debaters else None
        tab_card_data["debater_1"] = safe_name(deb1)
        tab_card_data["debater_1_status"] = get_debater_status(deb1)

        if len(debaters) > 1:
            deb2 = debaters[1]
        else:
            deb2 = None
        tab_card_data["debater_2"] = safe_name(deb2)
        tab_card_data["debater_2_status"] = get_debater_status(deb2)

        rounds = list(team.gov_team.all()) + list(team.opp_team.all())

        round_data = []
        for round_obj in rounds:
            if round_obj.victor != 0:  # Don't include rounds without a result
                round_data.append(
                    json_get_round(round_obj, team, deb1, deb2, bye_lookup=bye_lookup)
                )
        tab_card_data["rounds"] = round_data

        all_tab_cards_data[team.get_or_create_team_code()] = tab_card_data
    return all_tab_cards_data


def get_tab_card_data(request, team_id):
    try:
        team_id = int(team_id)
    except ValueError:
        return redirect_and_flash_error(request, "Invalid team id")

    # gov_team -> rounds where team is gov, opp_team -> rounds where team is opp
    team = Team.objects.prefetch_related("gov_team", "opp_team", "debaters").get(
        pk=team_id
    )
    rounds = list(Round.objects.filter(gov_team=team)) + list(
        Round.objects.filter(opp_team=team)
    )
    rounds.sort(key=lambda x: x.round_number)
    debaters = list(team.debaters.all())
    iron_man = len(debaters) == 1
    deb1 = debaters[0] if debaters else None
    if iron_man:
        deb2 = debaters[0]
    elif len(debaters) > 1:
        deb2 = debaters[1]
    else:
        deb2 = None

    num_rounds = TabSettings.get("tot_rounds", 0) or 0
    cur_round = TabSettings.get("cur_round", 1) or 1
    blank = " "
    round_stats = [[blank] * 7 for _ in range(num_rounds)]
    speaks_rolling = 0
    ranks_rolling = 0
    for round_obj in rounds:
        if round_obj.victor != 0:  # Don't include rounds without a result
            dstat1, dstat2 = get_dstats(round_obj, deb1, deb2, iron_man)
            if not dstat1:
                break
            side = GOV if round_obj.gov_team == team else OPP
            opponent = (
                round_obj.opp_team if round_obj.gov_team == team else round_obj.gov_team
            )
            opponent_name = (
                opponent.display_backend if opponent else "BYE"
            )
            judge_names = " - ".join(
                filter(None, (safe_name(j) for j in round_obj.judges.all()))
            )
            if dstat1:
                deb1_stats = (float(dstat1.speaks), float(dstat1.ranks))
            else:
                deb1_stats = blank
            if dstat2:
                deb2_stats = (float(dstat2.speaks), float(dstat2.ranks))
            else:
                deb2_stats = blank
            if dstat1 and dstat2:
                speaks_rolling += float(dstat1.speaks + dstat2.speaks)
                ranks_rolling += float(dstat1.ranks + dstat2.ranks)
                totals = (float(speaks_rolling), float(ranks_rolling))
            else:
                totals = blank
            index = round_obj.round_number - 1
            if 0 <= index < len(round_stats):
                round_stats[index] = [
                    side,
                    get_victor_label(round_obj.victor, side),
                    opponent_name,
                    judge_names,
                    deb1_stats,
                    deb2_stats,
                    totals,
                ]

    max_index = min(cur_round - 1, len(round_stats))
    for i in range(max_index):
        # Don't fill in totals for incomplete rounds
        if round_stats[i][6] == blank and len(round_stats)>=i+2 and blank not in round_stats[i + 1][:5]:
            round_stats[i][6] = (0, 0) if i == 0 else round_stats[i - 1][6]

    bye_round = (
        Bye.objects.filter(bye_team=team)
        .values_list("round_number", flat=True)
        .first()
    )

    return {
        "team_school": team.school,
        "debater_1": safe_name(deb1),
        "debater_1_status": get_debater_status(deb1),
        "debater_2": safe_name(deb2),
        "debater_2_status": get_debater_status(deb2),
        "round_stats": round_stats,
        "d1st": tot_speaks_deb(deb1) if deb1 else 0,
        "d1rt": tot_ranks_deb(deb1) if deb1 else 0,
        "d2st": tot_speaks_deb(deb2) if deb2 else 0,
        "d2rt": tot_ranks_deb(deb2) if deb2 else 0,
        "ts": tot_speaks(team),
        "tr": tot_ranks(team),
        "bye_round": bye_round,
    }


def csv_tab_cards(writer):
    # Write the CSV header row
    header = [
        "Team Name",
        "School",
        "Round id",
        "Round",
        "Gov/Opp",
        "Win/Loss",
        "Opponent",
        "Chair",
        "Wing(s)",
        "Debater 1",
        "N/V",
        "Speaks",
        "Ranks",
        "Debater 2",
        "N/V",
        "Speaks",
        "Ranks",
        "Total Speaks",
        "Total Ranks",
    ]
    writer.writerow(header)

    round_queryset = (
        Round.objects.select_related(
            "gov_team__school",
            "opp_team__school",
            "chair",
        )
        .prefetch_related(
            "judges",
            "gov_team__debaters",
            "opp_team__debaters",
            Prefetch("roundstats_set", queryset=RoundStats.objects.select_related("round")),
        )
        .order_by("round_number", "pk")
    )

    debater_queryset = Debater.objects.prefetch_related(
        Prefetch(
            "roundstats_set",
            queryset=RoundStats.objects.select_related("round"),
        ),
        "team_set",
        "team_set__no_shows",
    )

    teams = (
        Team.objects.select_related("school")
        .prefetch_related(
            Prefetch("debaters", queryset=debater_queryset),
            Prefetch("gov_team", queryset=round_queryset),
            Prefetch("opp_team", queryset=round_queryset),
            "no_shows",
            "byes",
        )
        .order_by("pk")
    )
    total_rounds = TabSettings.get("tot_rounds", 0) or 0

    for team in teams:
        team_code = team.get_or_create_team_code()
        team_school_name = safe_school_name(team) or ""
        debaters = list(team.debaters.all())
        deb1 = debaters[0] if debaters else None
        deb1_status = get_debater_status_short(deb1)
        iron_man = len(debaters) < 2
        if iron_man:
            deb2 = deb1
            deb2_status = deb1_status
        else:
            deb2 = debaters[1] if len(debaters) > 1 else None
            deb2_status = get_debater_status_short(deb2)

        rounds = list(team.gov_team.all()) + list(team.opp_team.all())
        round_data = [tuple() for _ in range(total_rounds)]

        for round_obj in rounds:
            side = GOV if round_obj.gov_team == team else OPP
            result = get_victor_label(round_obj.victor, side)
            opponent = (
                round_obj.opp_team if round_obj.gov_team == team else round_obj.gov_team
            )
            opponent_name = opponent.get_or_create_team_code() if opponent else "BYE"
            chair_name = safe_name(round_obj.chair) or ""
            judges = list(round_obj.judges.all())
            wings = " - ".join(
                filter(
                    None,
                    (
                        safe_name(judge)
                        for judge in judges
                        if not round_obj.chair_id or judge.pk != round_obj.chair_id
                    ),
                )
            )

            dstat1, dstat2 = get_dstats(
                round_obj, deb1, deb2, len(debaters) == 1)
            index = round_obj.round_number - 1
            if 0 <= index < len(round_data):
                round_data[index] = [
                    round_obj.pk,
                    round_obj.round_number,
                    side,
                    result,
                    opponent_name,
                    chair_name,
                    wings,
                    safe_name(deb1) or "",
                    deb1_status,
                    float(dstat1.speaks) if dstat1 else 0,
                    float(dstat1.ranks) if dstat1 else 0,
                    safe_name(deb2) or "",
                    deb2_status,
                    float(dstat2.speaks) if dstat2 else 0,
                    float(dstat2.ranks) if dstat2 else 0,
                ]

        # Write round data for this team
        for round_stat in round_data:
            if not round_stat:
                writer.writerow([team_code, team_school_name])
                continue
            writer.writerow(
                [
                    team_code,
                    team_school_name,
                    *round_stat,
                    # Round, Gov/Opp, Win/Loss, Opponent, Judges, Debater 1, N/V,
                    # Debater 1 S/R, Debater 2, N/V, Debater 2 S/R, Total
                ]
            )

        # Write the total stats for this team
        writer.writerow(
            [
                "",
                team_code,
                team_school_name,
                "Total",
                "",
                "",
                "",
                "",
                "",
                safe_name(deb1) or "",
                deb1_status,
                tot_speaks_deb(deb1) if deb1 else 0,
                tot_ranks_deb(deb1) if deb1 else 0,
                safe_name(deb2) or "",
                deb2_status,
                tot_speaks_deb(deb2) if deb2 else 0,
                tot_ranks_deb(deb2) if deb2 else 0,
                tot_speaks(team),
                tot_ranks(team),
            ]
        )
