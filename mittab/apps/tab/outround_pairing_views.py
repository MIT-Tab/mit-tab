import random
import time
import datetime

from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import logout
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, reverse

from mittab.apps.tab.helpers import redirect_and_flash_error, \
        redirect_and_flash_success
from mittab.apps.tab.team_views import get_team_rankings
from mittab.apps.tab.models import *
from mittab.libs.errors import *
from mittab.apps.tab.forms import ResultEntryForm, UploadBackupForm, score_panel, \
        validate_panel, EBallotForm
import mittab.libs.cache_logic as cache_logic
import mittab.libs.tab_logic as tab_logic
import mittab.libs.outround_tab_logic as outround_tab_logic
import mittab.libs.assign_judges as assign_judges
import mittab.libs.backup as backup


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
            request, "Success!", path="/"
        )

    # See if we can pair the round
    title = "Pairing Outrounds"
    current_round_number = 0

    previous_round_number = TabSettings.get("tot_rounds", 5)

    check_status = []
    
    judges = outround_tab_logic.have_enough_judges()
    rooms = outround_tab_logic.have_enough_rooms()
    
    msg = "Enough judges checked in for Out-rounds? Need {1}, have {2}".format(
        current_round_number, judges[1][1], judges[1][0])
    if judges[0]:
        check_status.append((msg, "Yes", "Judges are checked in"))
    else:
        check_status.append((msg, "No", "Not enough judges"))
        
    msg = "N/2 Rooms available Round Out-rounds? Need {1}, have {2}".format(
        current_round_number, rooms[1][1], rooms[1][0])
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
                          num_teams=TabSettings.get("var_teams_to_break", 8)):

    label = "[%s] Ro%s" % ("V" if type_of_round == BreakingTeam.VARSITY else "N",
                           num_teams)
    outround_options = []

    var_teams_to_break = TabSettings.get("var_teams_to_break", 8)
    nov_teams_to_break = TabSettings.get("nov_teams_to_break", 4)
    while var_teams_to_break > 1:
        if Outround.objects.filter(type_of_round=BreakingTeam.VARSITY,
                                   num_teams=var_teams_to_break).exists():
            outround_options.append(
                (reverse("outround_pairing_view", kwargs={"type_of_round": BreakingTeam.VARSITY,
                                                          "num_teams": var_teams_to_break}),
                 "[V] Ro%s" % (var_teams_to_break,))
            )
        var_teams_to_break /= 2

    while nov_teams_to_break > 1:
        if Outround.objects.filter(type_of_round=BreakingTeam.NOVICE,
                                   num_teams=nov_teams_to_break).exists():
            outround_options.append(
                (reverse("outround_pairing_view", kwargs={"type_of_round": BreakingTeam.NOVICE,
                                                          "num_teams": nov_teams_to_break}),
                 "[N] Ro%s" % (nov_teams_to_break,))
            )
        nov_teams_to_break /= 2
    
    outrounds = Outround.objects.filter(type_of_round=type_of_round,
                                        num_teams=num_teams).all()

    judges_per_panel = TabSettings.get("var_panel_size", 3) if type_of_round == BreakingTeam.VARSITY else TabSettings.get("nov_panel_size", 3)
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
        
    #pairing_released = TabSettings.get("pairing_released", 0) == 1
    
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
    
    size = max(list(map(len, [excluded_teams, excluded_judges, non_checkins, available_rooms])))
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

    other_round_type = BreakingTeam.NOVICE if round_obj.type_of_round == BreakingTeam.VARSITY else BreakingTeam.VARSITY

    excluded_judges = Judge.objects.exclude(judges_outrounds__num_teams=round_obj.num_teams,
                                            judges_outrounds__type_of_round=round_obj.type_of_round) \
                                   .exclude(judges_outrounds__num_teams=other_round_num,
                                            judges_outrounds__type_of_round=other_round_type) \
                                   .filter(checkin__round_number=0)

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

    breaking_teams_by_type = [t.team.id for t in BreakingTeam.objects.filter(type_of_team=current_team.breaking_team.type_of_team)]

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
