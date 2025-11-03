import random

from django.shortcuts import render, redirect
from django.urls import reverse

from mittab.apps.tab.helpers import redirect_and_flash_error
from mittab.apps.tab.models import (BreakingTeam, Bye, Outround,
                                    TabSettings, Judge, Team, Round)
from mittab.libs import cache_logic
from mittab.libs.tab_logic import rankings
from mittab.apps.tab.forms import EBallotForm
from mittab.libs.bracket_display_logic import get_bracket_data_json
from mittab.apps.tab.views.pairing_views import enter_result


def public_home(request):
    # Get current round and tournament status
    cur_round_setting = TabSettings.get("cur_round", 1)
    tot_rounds = TabSettings.get("tot_rounds", 5)
    pairing_released_inround = TabSettings.get("pairing_released", 0) == 1
    pairing_released = pairing_released_inround
    in_outrounds = False
    current_outround_label = ""

    # Before round 1 begins, keep the status neutral
    if cur_round_setting <= 1:
        pairing_released = False
        context = {
            "cur_round": cur_round_setting,
            "tot_rounds": tot_rounds,
            "pairing_released": pairing_released,
            "in_outrounds": in_outrounds,
            "current_outround_label": current_outround_label,
        }
        return render(request, "public/home.html", context)

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

    context = {
        "cur_round": cur_round_setting,
        "tot_rounds": tot_rounds,
        "pairing_released": pairing_released,
        "in_outrounds": in_outrounds,
        "current_outround_label": current_outround_label,
    }
    return render(request, "public/home.html", context)

def public_view_judges(request):
    display_judges = TabSettings.get("judges_public", 0)

    if not request.user.is_authenticated and not display_judges:
        return redirect_and_flash_error(request, "This view is not public", path=reverse("index"))

    num_rounds = TabSettings.get("tot_rounds", 5)
    rounds = [num for num in range(1, num_rounds + 1)]

    return render(
        request, "public/judges.html", {
            "judges": Judge.objects.order_by("name").prefetch_related("schools", "checkin_set").all(),
            "rounds": rounds
        })

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

def rank_teams_public(request):
    display_rankings = TabSettings.get("rankings_public", 0)

    if not display_rankings:
        return redirect_and_flash_error(request, "This view is not public", path=reverse("index"))

    teams = cache_logic.cache_fxn_key(
        rankings.get_team_rankings,
        "team_rankings_public",
        cache_logic.DEFAULT,
        request,
        public=True
    )

    return render(request, "public/public_team_rankings.html", {
        "teams": teams,
        "title": "Team Rankings"
    })

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
        bye.bye_team for bye in Bye.objects.filter(round_number=round_number).select_related('bye_team')
    ]
    team_count = len(paired_teams) + len(byes)

    for present_team in Team.objects.filter(checked_in=True).prefetch_related('debaters'):
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

    return render(request, "public/e_ballot_search.html")


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
