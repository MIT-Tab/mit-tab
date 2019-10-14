import random

from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import permission_required
from django.db.models import Q
from django.shortcuts import redirect, reverse

from mittab.apps.tab.helpers import redirect_and_flash_error, \
        redirect_and_flash_success
from mittab.apps.tab.team_views import get_team_rankings
from mittab.apps.tab.models import *
from mittab.libs.errors import *
from mittab.apps.tab.forms import OutroundResultEntryForm
import mittab.libs.cache_logic as cache_logic
import mittab.libs.tab_logic as tab_logic
import mittab.libs.outround_tab_logic as outround_tab_logic
import mittab.libs.assign_judges as assign_judges


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def pair_next_outround(request, num_teams, type_of_round):
    if request.method == "POST":
        Outround.objects.filter(num_teams__lt=num_teams,
                                type_of_round=type_of_round).delete()

        outround_tab_logic.pair(type_of_round)

        return redirect_and_flash_success(
            request, "Success!", path=reverse("outround_pairing_view",
                                              kwargs={
                                                  "num_teams": int(num_teams / 2),
                                                  "type_of_round": type_of_round
                                              }))

    # See if we can pair the round
    title = "Pairing Outrounds"
    current_round_number = 0

    previous_round_number = TabSettings.get("tot_rounds", 5)

    check_status = []

    judges = outround_tab_logic.have_enough_judges_type(type_of_round)
    rooms = outround_tab_logic.have_enough_rooms_type(type_of_round)

    msg = "Enough judges checked in for Out-rounds? Need {1}, have {2}".format(
        judges[1][1], judges[1][0])

    if num_teams <= 2:
        check_status.append(("Have more rounds?", "No", "Not enough teams"))
    else:
        check_status.append(("Have more rounds?", "Yes", "Have enough teams!"))

    if judges[0]:
        check_status.append((msg, "Yes", "Judges are checked in"))
    else:
        check_status.append((msg, "No", "Not enough judges"))

    msg = "N/2 Rooms available Round Out-rounds? Need {1}, have {2}".format(
        rooms[1][1], rooms[1][0])
    if rooms[0]:
        check_status.append((msg, "Yes", "Rooms are checked in"))
    else:
        check_status.append((msg, "No", "Not enough rooms"))

    round_label = "[%s] Ro%s" % ("N" if type_of_round else "V",
                                 num_teams)
    msg = "All Rounds properly entered for Round %s" % (
        round_label)
    ready_to_pair = "Yes"
    ready_to_pair_alt = "Checks passed!"
    try:
        outround_tab_logic.have_properly_entered_data(num_teams, type_of_round)
        check_status.append((msg, "Yes", "All rounds look good"))
    except PrevRoundNotEnteredError as e:
        ready_to_pair = "No"
        ready_to_pair_alt = str(e)
        check_status.append(
            (msg, "No", "Not all rounds are entered. %s" % str(e)))

    return render(request, "pairing/pair_round.html", locals())


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def break_teams(request):
    if request.method == "POST":
        # Perform the break
        teams, nov_teams = cache_logic.cache_fxn_key(
            get_team_rankings,
            "team_rankings",
            request
        )

        nov_teams_to_break = TabSettings.get("nov_teams_to_break", 4)
        var_teams_to_break = TabSettings.get("var_teams_to_break", 8)

        # This forces a refresh of the breaking teams
        Outround.objects.all().delete()
        BreakingTeam.objects.all().delete()

        current_seed = 1
        for team in teams:
            if current_seed > var_teams_to_break:
                break

            BreakingTeam.objects.create(team=team[0],
                                        seed=current_seed,
                                        effective_seed=current_seed,
                                        type_of_team=BreakingTeam.VARSITY)
            current_seed += 1

        current_seed = 1
        for nov_team in nov_teams:
            if current_seed > nov_teams_to_break:
                break

            if BreakingTeam.objects.filter(team=nov_team[0]).exists():
                continue

            BreakingTeam.objects.create(team=nov_team[0],
                                        seed=current_seed,
                                        effective_seed=current_seed,
                                        type_of_team=BreakingTeam.NOVICE)

            current_seed += 1

        outround_tab_logic.pair(BreakingTeam.VARSITY)
        outround_tab_logic.pair(BreakingTeam.NOVICE)

        return redirect_and_flash_success(
            request, "Success!", path="/outround_pairing"
        )

    # See if we can pair the round
    title = "Pairing Outrounds"
    current_round_number = 0

    previous_round_number = TabSettings.get("tot_rounds", 5)

    check_status = []

    judges = outround_tab_logic.have_enough_judges()
    rooms = outround_tab_logic.have_enough_rooms()

    msg = "Enough judges checked in for Out-rounds? Need {1}, have {2}".format(
        judges[1][1], judges[1][0])
    if judges[0]:
        check_status.append((msg, "Yes", "Judges are checked in"))
    else:
        check_status.append((msg, "No", "Not enough judges"))

    msg = "N/2 Rooms available Round Out-rounds? Need {1}, have {2}".format(
        rooms[1][1], rooms[1][0])
    if rooms[0]:
        check_status.append((msg, "Yes", "Rooms are checked in"))
    else:
        check_status.append((msg, "No", "Not enough rooms"))

    msg = "All Rounds properly entered for Round %s" % (
        previous_round_number)
    ready_to_pair = "Yes"
    ready_to_pair_alt = "Checks passed!"
    try:
        tab_logic.have_properly_entered_data(current_round_number)
        check_status.append((msg, "Yes", "All rounds look good"))
    except PrevRoundNotEnteredError as e:
        ready_to_pair = "No"
        ready_to_pair_alt = str(e)
        check_status.append(
            (msg, "No", "Not all rounds are entered. %s" % str(e)))
    except ByeAssignmentError as e:
        ready_to_pair = "No"
        ready_to_pair_alt = str(e)
        check_status.append(
            (msg, "No", "You have a bye and results. %s" % str(e)))
    except NoShowAssignmentError as e:
        ready_to_pair = "No"
        ready_to_pair_alt = str(e)
        check_status.append(
            (msg, "No", "You have a noshow and results. %s" % str(e)))

    return render(request, "pairing/pair_round.html", locals())


def outround_pairing_view(request,
                          type_of_round=BreakingTeam.VARSITY,
                          num_teams=None):

    if num_teams is None:
        return redirect("outround_pairing_view",
                        type_of_round=BreakingTeam.VARSITY,
                        num_teams=TabSettings.get("var_teams_to_break", 8))

    pairing_released = False

    if type_of_round == BreakingTeam.VARSITY:
        pairing_released = TabSettings.get("var_teams_visible", 256) <= num_teams
    elif type_of_round == BreakingTeam.NOVICE:
        pairing_released = TabSettings.get("nov_teams_visible", 256) <= num_teams

    label = "[%s] Ro%s" % ("V" if type_of_round == BreakingTeam.VARSITY else "N",
                           num_teams)
    outround_options = []

    var_teams_to_break = TabSettings.get("var_teams_to_break", 8)
    nov_teams_to_break = TabSettings.get("nov_teams_to_break", 4)
    while var_teams_to_break > 1:
        if Outround.objects.filter(type_of_round=BreakingTeam.VARSITY,
                                   num_teams=var_teams_to_break).exists():
            outround_options.append(
                (reverse("outround_pairing_view", kwargs={
                    "type_of_round": BreakingTeam.VARSITY,
                    "num_teams": int(var_teams_to_break)}),
                 "[V] Ro%s" % (int(var_teams_to_break),))
            )
        var_teams_to_break /= 2

    while nov_teams_to_break > 1:
        if Outround.objects.filter(type_of_round=BreakingTeam.NOVICE,
                                   num_teams=nov_teams_to_break).exists():
            outround_options.append(
                (reverse("outround_pairing_view", kwargs={
                    "type_of_round": BreakingTeam.NOVICE,
                    "num_teams": int(nov_teams_to_break)}),
                 "[N] Ro%s" % (nov_teams_to_break,))
            )
        nov_teams_to_break /= 2

    outrounds = Outround.objects.filter(type_of_round=type_of_round,
                                        num_teams=num_teams).all()

    judges_per_panel = TabSettings.get("var_panel_size", 3) \
                       if type_of_round == BreakingTeam.VARSITY \
                          else TabSettings.get("nov_panel_size", 3)
    judge_slots = [i for i in range(1, judges_per_panel + 1)]

    var_to_nov = TabSettings.get("var_to_nov", 2)
    other_round_num = num_teams / var_to_nov
    if type_of_round == BreakingTeam.NOVICE:
        other_round_num = num_teams * var_to_nov

    other_round_type = not type_of_round

    pairing_exists = len(outrounds) > 0

    lost_outrounds = [t.loser.id for t in Outround.objects.all() if t.loser]

    excluded_teams = BreakingTeam.objects.filter(
        type_of_team=type_of_round
    ).exclude(
        team__id__in=lost_outrounds
    )

    excluded_teams = [t.team for t in excluded_teams]

    excluded_teams = [t for t in excluded_teams if not Outround.objects.filter(
        type_of_round=type_of_round,
        num_teams=num_teams,
        gov_team=t
    ).exists()]

    excluded_teams = [t for t in excluded_teams if not Outround.objects.filter(
        type_of_round=type_of_round,
        num_teams=num_teams,
        opp_team=t
    ).exists()]

    excluded_judges = Judge.objects.exclude(
        judges_outrounds__num_teams=num_teams,
        judges_outrounds__type_of_round=type_of_round,
    ).exclude(
        judges_outrounds__type_of_round=other_round_type,
        judges_outrounds__num_teams=other_round_num
    ).filter(
        checkin__round_number=0
    )

    non_checkins = Judge.objects.exclude(
        judges_outrounds__num_teams=num_teams,
        judges_outrounds__type_of_round=type_of_round
    ).exclude(
        judges_outrounds__type_of_round=other_round_type,
        judges_outrounds__num_teams=other_round_num
    ).exclude(
        checkin__round_number=0
    )

    available_rooms = Room.objects.exclude(
        rooms_outrounds__num_teams=num_teams,
        rooms_outrounds__type_of_round=type_of_round
    ).exclude(
        rooms_outrounds__num_teams=other_round_num,
        rooms_outrounds__type_of_round=other_round_type
    ).exclude(rank=0)

    size = max(list(
        map(
            len,
            [excluded_teams, excluded_judges, non_checkins, available_rooms]
        )))
    # The minimum rank you want to warn on
    warning = 5
    excluded_people = list(
        zip(*[
            x + [""] * (size - len(x)) for x in [
                list(excluded_teams),
                list(excluded_judges),
                list(non_checkins),
                list(available_rooms)
            ]
        ]))

    return render(request,
                  "outrounds/pairing_base.html",
                  locals())


def alternative_judges(request, round_id, judge_id=None):
    round_obj = Outround.objects.get(id=int(round_id))
    round_gov, round_opp = round_obj.gov_team, round_obj.opp_team
    # All of these variables are for the convenience of the template
    try:
        current_judge_id = int(judge_id)
        current_judge_obj = Judge.objects.get(id=current_judge_id)
        current_judge_name = current_judge_obj.name
        current_judge_rank = current_judge_obj.rank
    except TypeError:
        current_judge_id, current_judge_obj, current_judge_rank = "", "", ""
        current_judge_name = "No judge"

    var_to_nov = TabSettings.get("var_to_nov", 2)
    other_round_num = round_obj.num_teams / var_to_nov
    if round_obj.type_of_round == BreakingTeam.NOVICE:
        other_round_num = round_obj.num_teams * var_to_nov

    other_round_type = BreakingTeam.NOVICE \
                       if round_obj.type_of_round == BreakingTeam.VARSITY \
                          else BreakingTeam.VARSITY

    excluded_judges = Judge.objects.exclude(
        judges_outrounds__num_teams=round_obj.num_teams,
        judges_outrounds__type_of_round=round_obj.type_of_round
    ).exclude(
        judges_outrounds__num_teams=other_round_num,
        judges_outrounds__type_of_round=other_round_type
    ).filter(
        checkin__round_number=0
    )

    query = Q(
        judges_outrounds__num_teams=round_obj.num_teams,
        judges_outrounds__type_of_round=round_obj.type_of_round
    )
    query = query | Q(
        judges_outrounds__num_teams=other_round_num,
        judges_outrounds__type_of_round=other_round_type
    )

    included_judges = Judge.objects.filter(query) \
                                   .filter(checkin__round_number=0) \
                                   .distinct()

    excluded_judges = [(j.name, j.id, float(j.rank))
                       for j in assign_judges.can_judge_teams(
                           excluded_judges, round_gov, round_opp)]
    included_judges = [(j.name, j.id, float(j.rank))
                       for j in assign_judges.can_judge_teams(
                           included_judges, round_gov, round_opp)]
    included_judges = sorted(included_judges, key=lambda x: -x[2])
    excluded_judges = sorted(excluded_judges, key=lambda x: -x[2])

    return render(request, "pairing/judge_dropdown.html", locals())


def alternative_teams(request, round_id, current_team_id, position):
    round_obj = Outround.objects.get(pk=round_id)
    current_team = Team.objects.get(pk=current_team_id)

    breaking_teams_by_type = [t.team.id
                              for t in BreakingTeam.objects.filter(
                                  type_of_team=current_team.breaking_team.type_of_team
                              )]

    excluded_teams = Team.objects.filter(
        id__in=breaking_teams_by_type
    ).exclude(
        gov_team_outround__num_teams=round_obj.num_teams
    ).exclude(
        opp_team_outround__num_teams=round_obj.num_teams
    ).exclude(pk=current_team_id)

    included_teams = Team.objects.filter(
        id__in=breaking_teams_by_type
    ).exclude(
        pk__in=excluded_teams
    )

    return render(request, "pairing/team_dropdown.html", locals())


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def assign_team(request, round_id, position, team_id):
    try:
        round_obj = Outround.objects.get(id=int(round_id))
        team_obj = Team.objects.get(id=int(team_id))

        if position.lower() == "gov":
            round_obj.gov_team = team_obj
        elif position.lower() == "opp":
            round_obj.opp_team = team_obj
        else:
            raise ValueError("Got invalid position: " + position)
        round_obj.save()

        data = {
            "success": True,
            "team": {
                "id": team_obj.id,
                "name": team_obj.name
            },
        }
    except Exception:
        emit_current_exception()
        data = {"success": False}
    return JsonResponse(data)


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def assign_judge(request, round_id, judge_id, remove_id=None):
    try:
        round_obj = Outround.objects.get(id=int(round_id))
        judge_obj = Judge.objects.get(id=int(judge_id))
        round_obj.judges.add(judge_obj)

        if remove_id is not None:
            remove_obj = Judge.objects.get(id=int(remove_id))
            round_obj.judges.remove(remove_obj)

            if remove_obj == round_obj.chair:
                round_obj.chair = round_obj.judges.order_by("-rank").first()
        elif not round_obj.chair:
            round_obj.chair = judge_obj

        round_obj.save()
        data = {
            "success": True,
            "chair_id": round_obj.chair.id,
            "round_id": round_obj.id,
            "judge_name": judge_obj.name,
            "judge_rank": float(judge_obj.rank),
            "judge_id": judge_obj.id
        }
    except Exception:
        emit_current_exception()
        data = {"success": False}
    return JsonResponse(data)


def enter_result(request,
                 round_id,
                 form_class=OutroundResultEntryForm):

    round_obj = Outround.objects.get(id=round_id)

    redirect_to = reverse("outround_pairing_view",
                          kwargs={
                              "num_teams": round_obj.num_teams,
                              "type_of_round": round_obj.type_of_round
                          })

    if request.method == "POST":
        form = form_class(request.POST, round_instance=round_obj)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request, "Invalid round result, could not remedy.")
            return redirect_and_flash_success(request,
                                              "Result entered successfully",
                                              path=redirect_to)
    else:
        form_kwargs = {"round_instance": round_obj}
        form = form_class(**form_kwargs)

    return render(
        request, "outrounds/ballot.html", {
            "form": form,
            "title": "Entering Ballot for {}".format(round_obj),
            "gov_team": round_obj.gov_team,
            "opp_team": round_obj.opp_team,
        })


def pretty_pair(request, type_of_round=BreakingTeam.VARSITY, printable=False):
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

    outround_pairings = [
        {
            "label": "[%s] Ro%s" % ("N" if type_of_round else "V", value),
            "rounds": Outround.objects.filter(num_teams=value,
                                              type_of_round=type_of_round)
        } for value in unique_values
    ]

    label = "%s Outrounds Pairings" % ("Novice" if type_of_round else "Varsity",)

    round_pairing = list(round_pairing)

    #We want a random looking, but constant ordering of the rounds
    random.seed(0xBEEF)
    random.shuffle(round_pairing)
    round_pairing.sort(key=lambda r: r.gov_team.name)
    paired_teams = [team.gov_team for team in round_pairing
                    ] + [team.opp_team for team in round_pairing]

    team_count = len(paired_teams)

    pairing_exists = True
    #pairing_exists = TabSettings.get("pairing_released", 0) == 1
    printable = printable
    return render(request, "outrounds/pretty_pairing.html", locals())


def pretty_pair_print(request, type_of_round=BreakingTeam.VARSITY):
    return pretty_pair(request, type_of_round, True)


def toggle_pairing_released(request, type_of_round, num_teams):
    old = 256

    if type_of_round == BreakingTeam.VARSITY:
        old = TabSettings.get("var_teams_visible", 256)

        if old == num_teams:
            TabSettings.set("var_teams_visible", num_teams * 2)
        else:
            TabSettings.set("var_teams_visible", num_teams)
    else:
        old = TabSettings.get("nov_teams_visible", 256)

        if old == num_teams:
            TabSettings.set("nov_teams_visible", num_teams * 2)
        else:
            TabSettings.set("nov_teams_visible", num_teams)

    data = {"success": True, "pairing_released": not old == num_teams}
    return JsonResponse(data)
