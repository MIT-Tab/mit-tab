import random

from django.db.models import Prefetch
from django.shortcuts import redirect, render
from django.urls import reverse

from mittab.apps.tab.forms import EBallotForm
from mittab.apps.tab.helpers import redirect_and_flash_error
from mittab.apps.tab.models import (
    BreakingTeam,
    Bye,
    Debater,
    Outround,
    TabSettings,
    Judge,
    Team,
    Round,
    RoundStats,
)
from mittab.apps.tab.public_rankings import (
    get_all_ballot_round_settings,
    get_ranking_settings,
)
from mittab.apps.tab.views.debater_views import get_speaker_rankings
from mittab.apps.tab.views.pairing_views import enter_result
from mittab.libs.bracket_display_logic import get_bracket_data_json
from mittab.libs.cacheing.public_cache import cache_public_view
from mittab.libs.tab_logic import rankings


@cache_public_view(timeout=300)
def public_access_error(request):
    return render(request, "public/access_error.html")


@cache_public_view(timeout=60)
def public_home(request):
    cur_round_setting = TabSettings.get("cur_round", 1) - 1
    tot_rounds = TabSettings.get("tot_rounds", 5)
    pairing_released_inround = TabSettings.get("pairing_released", 0) == 1
    pairing_released = pairing_released_inround
    in_outrounds = False
    current_outround_label = ""

    if cur_round_setting < 1:
        pairing_released = False
        status_primary = "Tournament"
        status_secondary = "Starting soon"
        return render(
            request,
            "public/home.html",
            {
                "status_primary": status_primary,
                "status_secondary": status_secondary,
            },
        )

    outround_qs = Outround.objects.order_by("num_teams")

    if outround_qs.exists():
        varsity = (
            outround_qs.filter(type_of_round=BreakingTeam.VARSITY)
            .order_by("num_teams")
            .first()
        )
        novice = (
            outround_qs.filter(type_of_round=BreakingTeam.NOVICE)
            .order_by("num_teams")
            .first()
        )

        labels = []
        release_flags = []

        if varsity:
            labels.append(f"[V] Ro{varsity.num_teams}")
            release_flags.append(
                TabSettings.get("var_teams_visible", 256) <= varsity.num_teams
            )
        if novice:
            labels.append(f"[N] Ro{novice.num_teams}")
            release_flags.append(
                TabSettings.get("nov_teams_visible", 256) <= novice.num_teams
            )

        if labels:
            in_outrounds = True
            current_outround_label = " & ".join(labels)
            pairing_released = bool(release_flags and all(release_flags))
        else:
            pairing_released = pairing_released_inround

    pairing_text = "Pairing released" if pairing_released else "Pairing in progress"

    if in_outrounds:
        status_primary = current_outround_label or "Elimination rounds"
        status_secondary = pairing_text
    elif cur_round_setting <= tot_rounds:
        status_primary = f"Round {cur_round_setting}"
        status_secondary = pairing_text
    else:
        status_primary = "Tournament"
        status_secondary = pairing_text

    return render(
        request,
        "public/home.html",
        {
            "status_primary": status_primary,
            "status_secondary": status_secondary,
        },
    )

@cache_public_view(timeout=60)
def public_view_judges(request):
    display_judges = TabSettings.get("judges_public", 0)

    if not display_judges:
        return redirect("public_access_error")

    num_rounds = TabSettings.get("tot_rounds", 5)
    rounds = [num for num in range(1, num_rounds + 1)]

    return render(
        request,
        "public/judges.html",
        {
            "judges": Judge.objects.order_by("name")
            .prefetch_related("schools", "checkin_set")
            .all(),
            "rounds": rounds,
        },
    )


@cache_public_view(timeout=60)
def public_view_teams(request):
    display_teams = TabSettings.get("teams_public", 0)

    if not request.user.is_authenticated and not display_teams:
        return redirect_and_flash_error(
            request, "This view is not public", path=reverse("index"))

    return render(
        request, "public/teams.html", {
            "teams": Team.objects
                     .order_by("-checked_in", "school__name")
                     .prefetch_related("debaters", "school", "hybrid_school")
                     .all(),
            "num_checked_in": Team.objects.filter(checked_in=True).count()
        })


@cache_public_view(timeout=60)
def rank_teams_public(request):
    settings = get_ranking_settings("team")
    if not settings["public"]:
        return redirect("public_access_error")

    up_to_round = settings.get("up_to_round") or 0
    teams = rankings.get_team_rankings(request, public=True, up_to_round=up_to_round)
    rows = build_public_team_rows(teams, settings["include_speaks"])
    rows = rows[:settings["max_visible"]]

    return render(
        request,
        "public/public_team_results.html",
        {
            "show_scores": settings["include_speaks"],
            "public_team_rows": rows,
            "title": "Team Rankings",
        },
    )


@cache_public_view(timeout=60)
def public_speaker_rankings(request):
    ranking_configs = {
        "varsity": get_ranking_settings("varsity"),
        "novice": get_ranking_settings("novice"),
    }

    if not any(config["public"] for config in ranking_configs.values()):
        return redirect("public_access_error")

    varsity_speakers, novice_speakers = get_speaker_rankings(None)
    speaker_lists = {
        "varsity": [
            entry for entry in varsity_speakers
            if entry[0].novice_status == Debater.VARSITY
        ],
        "novice": novice_speakers,
    }
    rows = {
        slug: build_public_speaker_rows(
            speaker_lists[slug],
            config["include_speaks"],
            config["max_visible"],
        )
        for slug, config in ranking_configs.items()
    }
    sections = [{
        "title": "Varsity Speakers" if slug == "varsity" else "Novice Speakers",
        "rows": rows[slug],
        "show": ranking_configs[slug]["public"],
        "show_scores": ranking_configs[slug]["include_speaks"],
        "empty_message": "No varsity speakers are available yet."
        if slug == "varsity"
        else "No novice speakers are available yet.",
    } for slug in ("varsity", "novice")]

    return render(
        request,
        "public/public_speaker_rankings.html",
        {
            "speaker_sections": sections,
        },
    )


@cache_public_view(timeout=60)
def public_ballots(request):
    tot_rounds = int(TabSettings.get("tot_rounds", 0) or 0)
    ballot_settings = get_all_ballot_round_settings(tot_rounds)
    visible_rounds = [setting for setting in ballot_settings if setting["visible"]]

    if not visible_rounds:
        return redirect("public_access_error")

    round_results = [{
        "round_number": setting["round_number"],
        "ballots": build_public_ballots_for_round(setting["round_number"]),
        "include_speaks": setting["include_speaks"],
        "include_ranks": setting["include_ranks"],
    } for setting in sorted(
        visible_rounds,
        key=lambda setting: setting["round_number"],
        reverse=True,
    )]

    return render(
        request,
        "public/public_ballots.html",
        {"round_results": round_results},
    )


def build_public_ballots_for_round(round_number):
    completed_victors = (
        Round.GOV,
        Round.OPP,
        Round.GOV_VIA_FORFEIT,
        Round.OPP_VIA_FORFEIT,
    )

    rounds = (
        Round.objects.filter(
            round_number=round_number,
            victor__in=completed_victors,
            gov_team__ranking_public=True,
            opp_team__ranking_public=True,
        )
        .select_related("gov_team", "opp_team")
        .prefetch_related(
            "gov_team__debaters",
            "opp_team__debaters",
            Prefetch(
                "roundstats_set",
                queryset=RoundStats.objects.select_related("debater"),
            ),
        )
        .order_by("gov_team__name", "opp_team__name")
    )

    return [serialize_round_for_public(round_obj) for round_obj in rounds]


def build_public_team_rows(teams, show_scores):
    rows = [{
        "team": entry[0],
        "wins": entry[1],
        "speaks": entry[2] if show_scores else None,
        "ranks": entry[3] if show_scores else None,
        "debaters": entry[0].debaters_display(),
    } for entry in teams]

    if show_scores:
        for idx, row in enumerate(rows, start=1):
            row["place"] = idx
        return rows

    grouped = {}
    for row in rows:
        grouped.setdefault(row["wins"], []).append(row)

    ordered_rows = []
    rng = random.Random(0xC0FFEE)
    place_counter = 1

    for wins in sorted(grouped.keys(), reverse=True):
        group = grouped[wins]
        rng.shuffle(group)
        for row in group:
            row["place"] = place_counter
        ordered_rows.extend(group)
        place_counter += len(group)

    return ordered_rows


def build_public_speaker_rows(speakers, show_scores, max_visible):
    rows = []
    for idx, entry in enumerate(speakers[:max_visible], start=1):
        # get_speaker_rankings now returns 5-tuples:
        # (debater, speaks, ranks, team, tiebreaker). Older data may omit the
        # tiebreaker, so gracefully handle either shape.
        if len(entry) == 5:
            debater, speaks, ranks, team, _tiebreaker = entry
        else:
            debater, speaks, ranks, team = entry
        rows.append({
            "place": idx,
            "debater": debater,
            "speaks": speaks if show_scores else None,
            "ranks": ranks if show_scores else None,
            "team": team,
        })
    return rows


def serialize_round_for_public(round_obj):
    stats_by_debater = {
        stat.debater_id: stat for stat in round_obj.roundstats_set.all()
    }

    winner = None
    winner_side = None
    if round_obj.victor in (Round.GOV, Round.GOV_VIA_FORFEIT):
        winner = round_obj.gov_team
        winner_side = "Gov"
    elif round_obj.victor in (Round.OPP, Round.OPP_VIA_FORFEIT):
        winner = round_obj.opp_team
        winner_side = "Opp"

    sides = [{
        "label": "Gov",
        "team_name": round_obj.gov_team.display,
        "is_winner": winner_side == "Gov",
        "debaters": serialize_debaters(round_obj.gov_team, stats_by_debater),
    }, {
        "label": "Opp",
        "team_name": round_obj.opp_team.display,
        "is_winner": winner_side == "Opp",
        "debaters": serialize_debaters(round_obj.opp_team, stats_by_debater),
    }]

    return {
        "round_number": round_obj.round_number,
        "round_label": f"Round {round_obj.round_number}",
        "winner_name": winner.display if winner else None,
        "winner_side": winner_side,
        "victor_display": round_obj.get_victor_display(),
        "sides": sides,
    }


def serialize_debaters(team, stats_by_debater):
    return [{
        "name": debater.name,
        "speaks": getattr(stats_by_debater.get(debater.id), "speaks", None),
        "ranks": getattr(stats_by_debater.get(debater.id), "ranks", None),
    } for debater in team.debaters.all()]

@cache_public_view(timeout=60)
def pretty_pair(request):
    errors, byes = [], []

    round_number = TabSettings.get("cur_round") - 1
    round_pairing = list(
        Round.objects.filter(round_number=round_number).prefetch_related(
            "gov_team",
            "opp_team",
            "chair",
            "judges",
            "room",
            "gov_team__debaters",
            "opp_team__debaters",
        )
    )

    # We want a random looking, but constant ordering of the rounds
    random.seed(0xBEEF)
    random.shuffle(round_pairing)
    round_pairing.sort(key=lambda r: r.gov_team.name)
    paired_teams = [team.gov_team for team in round_pairing
                    ] + [team.opp_team for team in round_pairing]

    byes = [
        bye.bye_team
        for bye in Bye.objects.filter(round_number=round_number).select_related(
            "bye_team"
        )
    ]
    team_count = len(paired_teams) + len(byes)

    for present_team in Team.objects.filter(checked_in=True).prefetch_related(
        "debaters"
    ):
        if present_team not in paired_teams:
            if present_team not in byes:
                errors.append(present_team)

    pairing_exists = TabSettings.get("pairing_released", 0) == 1
    debater_team_memberships_public = TabSettings.get("debaters_public", 1)
    context = {
        "errors": errors,
        "byes": byes,
        "round_number": round_number,
        "round_pairing": round_pairing,
        "paired_teams": paired_teams,
        "team_count": team_count,
        "pairing_exists": pairing_exists,
        "debater_team_memberships_public": debater_team_memberships_public,
    }
    return render(request, "public/pairing_display.html", context)


@cache_public_view(timeout=30)
def missing_ballots(request):
    round_number = TabSettings.get("cur_round") - 1
    rounds = Round.objects.prefetch_related("gov_team", "opp_team",
                                            "room", "chair") \
        .filter(victor=Round.NONE, round_number=round_number)
    # need to do this to not reveal brackets

    rounds = sorted(rounds, key=lambda r: r.chair.name if r.chair else "")
    pairing_exists = TabSettings.get("pairing_released", 0) == 1
    return render(
        request,
        "public/missing_ballots.html",
        {
            "rounds": rounds,
            "pairing_exists": pairing_exists,
        },
    )


def e_ballot_search_page(request):
    return render(request, "public/e_ballot_search.html")


def e_ballot_search(request):
    if request.method == "POST":
        ballot_code = (request.POST.get("ballot_code") or "").strip()
        if ballot_code:
            return redirect("enter_e_ballot", ballot_code=ballot_code)
        return redirect_and_flash_error(
            request,
            "Please enter the ballot code provided by tab.",
            path=reverse("e_ballot_search"),
        )

    return e_ballot_search_page(request)


def enter_e_ballot(request, ballot_code):
    if request.method == "POST":
        round_id = request.POST.get("round_instance")

        if round_id:
            return enter_result(request,
                                round_id,
                                EBallotForm,
                                ballot_code,
                                redirect_to="/")
        else:
            message = """
                      Missing necessary form data. Please go to tab if this
                      error persists
                      """

    current_round = TabSettings.get(key="cur_round") - 1

    judge = Judge.objects.filter(ballot_code=ballot_code).prefetch_related(
        # bad use of related_name in the model, this gets the rounds
        "judges",
    ).first()

    if not judge:
        message = f"""
                    No judges with the ballot code "{ballot_code}." Try submitting again, or
                    go to tab to resolve the issue.
                    """
    elif TabSettings.get("pairing_released", 0) != 1:
        message = "Pairings for this round have not been released."
    else:
        # see above, judge.judges is rounds
        rounds = list(judge.judges.prefetch_related("chair")
                      .filter(round_number=current_round).all())
        if len(rounds) > 1:
            message = """
                    Found more than one ballot for you this round.
                    Go to tab to resolve this error.
                    """
        elif not rounds:
            message = """
                    Could not find a ballot for you this round. Go to tab
                    to resolve the issue if you believe you were paired in.
                    """
        elif rounds[0].chair != judge:
            message = """
                    You are not the chair of this round. If you are on a panel,
                    only the chair can submit an e-ballot. If you are not on a
                    panel, go to tab and make sure the chair is properly set for
                    the round.
                    """
        else:
            return enter_result(request, rounds[0].id, EBallotForm, ballot_code)
    return redirect_and_flash_error(request, message, path=reverse("tab_login"))


@cache_public_view(timeout=60)
def outround_pretty_pair(request, type_of_round=BreakingTeam.VARSITY):
    gov_opp_display = TabSettings.get("gov_opp_display", 0)

    round_number = 256

    if type_of_round == BreakingTeam.VARSITY:
        round_number = TabSettings.get("var_teams_visible", 256)
    else:
        round_number = TabSettings.get("nov_teams_visible", 256)

    round_pairing = Outround.objects.filter(
        num_teams__gte=round_number,
        type_of_round=type_of_round
    )

    unique_values = round_pairing.values_list("num_teams")
    unique_values = list(set([value[0] for value in unique_values]))
    unique_values.sort(key=lambda v: v, reverse=True)

    outround_pairings = []

    for value in unique_values:
        lost_outrounds = [t.loser.id for t in Outround.objects.all() if t.loser]

        excluded_teams = BreakingTeam.objects.filter(
            type_of_team=type_of_round
        ).exclude(
            team__id__in=lost_outrounds
        )

        excluded_teams = [t.team for t in excluded_teams]

        excluded_teams = [t for t in excluded_teams if not Outround.objects.filter(
            type_of_round=type_of_round,
            num_teams=value,
            gov_team=t
        ).exists()]

        excluded_teams = [t for t in excluded_teams if not Outround.objects.filter(
            type_of_round=type_of_round,
            num_teams=value,
            opp_team=t
        ).exists()]

        outround_pairings.append({
            "label": f"[{'N' if type_of_round else 'V'}] Ro{value}",
            "rounds": Outround.objects.filter(num_teams=value,
                                              type_of_round=type_of_round),
            "excluded": excluded_teams
        })

    label = f"{'Novice' if type_of_round else 'Varsity'} Outrounds Pairings"

    round_pairing = list(round_pairing)

    # We want a random looking, but constant ordering of the rounds
    random.seed(0xBEEF)
    random.shuffle(round_pairing)
    round_pairing.sort(key=lambda r: r.gov_team.name)
    paired_teams = [team.gov_team for team in round_pairing
                    ] + [team.opp_team for team in round_pairing]

    team_count = len(paired_teams)

    pairing_exists = True
    #pairing_exists = TabSettings.get("pairing_released", 0) == 1

    sidelock = TabSettings.get("sidelock", 0)
    choice = TabSettings.get("choice", 0)
    debater_team_memberships_public = TabSettings.get("debaters_public", 1)
    show_outrounds_bracket = TabSettings.get("show_outs_bracket", False)
    bracket_data_json = None
    if outround_pairings and show_outrounds_bracket:
        bracket_data_json = get_bracket_data_json(outround_pairings)

    context = {
        "gov_opp_display": gov_opp_display,
        "round_number": round_number,
        "type_of_round": type_of_round,
        "round_pairing": round_pairing,
        "outround_pairings": outround_pairings,
        "label": label,
        "paired_teams": paired_teams,
        "team_count": team_count,
        "pairing_exists": pairing_exists,
        "sidelock": sidelock,
        "choice": choice,
        "debater_team_memberships_public": debater_team_memberships_public,
        "show_outrounds_bracket": show_outrounds_bracket,
        "bracket_data_json": bracket_data_json,
    }

    return render(request, "public/outround_pairing.html", context)
