import random
import math

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import permission_required
from django.db.models import Q, Exists, OuterRef, Min
from django.shortcuts import redirect, reverse

from mittab.apps.tab.helpers import redirect_and_flash_error, \
    redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs import assign_judges
from mittab.libs.errors import *
from mittab.apps.tab.forms import OutroundResultEntryForm
import mittab.libs.tab_logic as tab_logic
import mittab.libs.outround_tab_logic as outround_tab_logic
from mittab.libs.outround_tab_logic import offset_to_quotient
from mittab.libs.bracket_display_logic import get_bracket_data_json
import mittab.libs.backup as backup
from mittab.libs.data_export.pairings_export import export_pairings_csv


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def pair_next_outround(request, num_teams, type_of_round):
    if request.method == "POST":
        type_str = "Varsity" if type_of_round == Outround.VARSITY else "Novice"
        round_str = f"Round-of-{num_teams}-{type_str}"
        backup.backup_round(round_number=round_str,
                            btype=backup.BEFORE_PAIRING)

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

    msg = (
        "Enough judges checked in for Out-rounds? "
        f"Need {judges[1][1]}, have {judges[1][0]}"
    )

    if num_teams <= 2:
        check_status.append(("Have more rounds?", "No", "Not enough teams"))
    else:
        check_status.append(("Have more rounds?", "Yes", "Have enough teams!"))

    if judges[0]:
        check_status.append((msg, "Yes", "Judges are checked in"))
    else:
        check_status.append((msg, "No", "Not enough judges"))

    msg = (
        "N/2 Rooms available Round Out-rounds? "
        f"Need {rooms[1][1]}, have {rooms[1][0]}"
    )
    if rooms[0]:
        check_status.append((msg, "Yes", "Rooms are checked in"))
    else:
        check_status.append((msg, "No", "Not enough rooms"))

    round_label = f"[{'N' if type_of_round else 'V'}] Ro{num_teams}"
    msg = f"All Rounds properly entered for Round {round_label}"
    ready_to_pair = "Yes"
    ready_to_pair_alt = "Checks passed!"
    try:
        outround_tab_logic.have_properly_entered_data(num_teams, type_of_round)
        check_status.append((msg, "Yes", "All rounds look good"))
    except PrevRoundNotEnteredError as e:
        ready_to_pair = "No"
        ready_to_pair_alt = str(e)
        check_status.append(
            (msg, "No", f"Not all rounds are entered. {e}"))

    context = {
        "title": title,
        "current_round_number": current_round_number,
        "previous_round_number": previous_round_number,
        "check_status": check_status,
        "judges": judges,
        "rooms": rooms,
        "ready_to_pair": ready_to_pair,
        "ready_to_pair_alt": ready_to_pair_alt,
        "num_teams": num_teams,
        "type_of_round": type_of_round,
        "round_label": round_label,
    }
    return render(request, "pairing/pair_round.html", context)


def get_outround_options(var_teams_to_break,
                         nov_teams_to_break):
    outround_options = []

    while not math.log(var_teams_to_break, 2) % 1 == 0:
        var_teams_to_break += 1

    while not math.log(nov_teams_to_break, 2) % 1 == 0:
        nov_teams_to_break += 1

    while var_teams_to_break > 1:
        if Outround.objects.filter(type_of_round=BreakingTeam.VARSITY,
                                   num_teams=var_teams_to_break).exists():
            outround_options.append(
                (reverse("outround_pairing_view", kwargs={
                    "type_of_round": BreakingTeam.VARSITY,
                    "num_teams": int(var_teams_to_break)}),
                 f"[V] Ro{int(var_teams_to_break)}")
            )
        var_teams_to_break /= 2

    while nov_teams_to_break > 1:
        if Outround.objects.filter(type_of_round=BreakingTeam.NOVICE,
                                   num_teams=nov_teams_to_break).exists():
            outround_options.append(
                (reverse("outround_pairing_view", kwargs={
                    "type_of_round": BreakingTeam.NOVICE,
                    "num_teams": int(nov_teams_to_break)}),
                 f"[N] Ro{int(nov_teams_to_break)}")
            )
        nov_teams_to_break /= 2

    return outround_options


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def break_teams(request):
    if request.method == "POST":
        # Perform the break
        backup.backup_round(btype=backup.BEFORE_BREAK)

        success, msg = outround_tab_logic.perform_the_break()

        if success:
            return redirect_and_flash_success(
                request, msg, path="/outround_pairing"
            )
        return redirect_and_flash_error(
            request, msg, path="/"
        )

    # See if we can pair the round
    title = "Pairing Outrounds"
    current_round_number = 0

    previous_round_number = TabSettings.get("tot_rounds", 5)

    check_status = []

    msg = f"All Rounds properly entered for Round {previous_round_number}"

    ready_to_pair = "Yes"
    ready_to_pair_alt = "Checks passed!"
    try:
        tab_logic.have_properly_entered_data(current_round_number)
        check_status.append((msg, "Yes", "All rounds look good"))
    except PrevRoundNotEnteredError as e:
        ready_to_pair = "No"
        ready_to_pair_alt = str(e)
        check_status.append(
            (msg, "No", f"Not all rounds are entered. {e}"))
    except ByeAssignmentError as e:
        ready_to_pair = "No"
        ready_to_pair_alt = str(e)
        check_status.append(
            (msg, "No", f"You have a bye and results. {e}"))
    except NoShowAssignmentError as e:
        ready_to_pair = "No"
        ready_to_pair_alt = str(e)
        check_status.append(
            (msg, "No", f"You have a noshow and results. {e}"))

    rooms = outround_tab_logic.have_enough_rooms_before_break()
    msg = (
        "N/2 Rooms available Round Out-rounds? "
        f"Need {rooms[1][1]}, have {rooms[1][0]}"
    )
    if rooms[0]:
        check_status.append((msg, "Yes", "Rooms are checked in"))
    else:
        check_status.append((msg, "No", "Not enough rooms"))

    context = {
        "title": title,
        "current_round_number": current_round_number,
        "previous_round_number": previous_round_number,
        "check_status": check_status,
        "ready_to_pair": ready_to_pair,
        "ready_to_pair_alt": ready_to_pair_alt,
        "rooms": rooms,
    }
    return render(request, "pairing/pair_round.html", context)


def outround_pairing_view(request,
                          type_of_round=BreakingTeam.VARSITY,
                          num_teams=None):

    choice = TabSettings.get("choice", 0)

    if num_teams is None:
        num_teams = TabSettings.get("var_teams_to_break", 8)

        while not math.log(num_teams, 2) % 1 == 0:
            num_teams += 1

        return redirect("outround_pairing_view",
                        type_of_round=BreakingTeam.VARSITY,
                        num_teams=num_teams)

    pairing_released = False

    if type_of_round == BreakingTeam.VARSITY:
        pairing_released = TabSettings.get("var_teams_visible", 256) <= num_teams
    elif type_of_round == BreakingTeam.NOVICE:
        pairing_released = TabSettings.get("nov_teams_visible", 256) <= num_teams

    label = (
        f"[{'V' if type_of_round == BreakingTeam.VARSITY else 'N'}] Ro{num_teams}"
    )
    nov_teams_to_break = TabSettings.get("nov_teams_to_break")
    var_teams_to_break = TabSettings.get("var_teams_to_break")

    if not nov_teams_to_break or not var_teams_to_break:
        return redirect_and_flash_error(request,
                                        "Please check your break tab settings",
                                        path="/")

    outround_options = get_outround_options(var_teams_to_break,
                                            nov_teams_to_break)

    outrounds = Outround.objects.filter(type_of_round=type_of_round,
                                        num_teams=num_teams).all()

    judges_per_panel = TabSettings.get("var_panel_size", 3) \
        if type_of_round == BreakingTeam.VARSITY \
        else TabSettings.get("nov_panel_size", 3)
    judge_slots = [i for i in range(1, judges_per_panel + 1)]

    var_to_nov = TabSettings.get("var_to_nov", 2)

    var_to_nov = offset_to_quotient(var_to_nov)

    other_round_num = num_teams / var_to_nov
    if type_of_round == BreakingTeam.NOVICE:
        other_round_num = num_teams * var_to_nov

    other_round_type = BreakingTeam.VARSITY \
        if type_of_round == BreakingTeam.NOVICE \
        else BreakingTeam.NOVICE

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
    )

    checked_in_rooms = [r.room for r in RoomCheckIn.objects.filter(round_number=0)]
    available_rooms = [r for r in available_rooms if r in checked_in_rooms]

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

    context = {
        "choice": choice,
        "type_of_round": type_of_round,
        "num_teams": num_teams,
        "pairing_released": pairing_released,
        "label": label,
        "outround_options": outround_options,
        "outrounds": outrounds,
        "judges_per_panel": judges_per_panel,
        "judge_slots": judge_slots,
        "var_to_nov": var_to_nov,
        "other_round_num": other_round_num,
        "other_round_type": other_round_type,
        "pairing_exists": pairing_exists,
        "excluded_teams": excluded_teams,
        "excluded_judges": excluded_judges,
        "non_checkins": non_checkins,
        "available_rooms": available_rooms,
        "size": size,
        "warning": warning,
        "excluded_people": excluded_people,
    }

    return render(
        request,
        "outrounds/pairing_base.html",
        context,
    )


def alternative_judges(request, round_id, judge_id=None):
    round_obj = Outround.objects.get(id=int(round_id))
    round_gov, round_opp = round_obj.gov_team, round_obj.opp_team
    # All of these variables are for the convenience of the template
    try:
        current_judge_id = int(judge_id)
        current_judge_obj = Judge.objects.prefetch_related("scratches").get(
            id=current_judge_id
        )
        current_judge_name = current_judge_obj.name
        current_judge_rank = current_judge_obj.rank
    except TypeError:
        current_judge_id, current_judge_obj, current_judge_rank = "", "", ""
        current_judge_name = "No judge"

    var_to_nov = TabSettings.get("var_to_nov", 2)

    var_to_nov = offset_to_quotient(var_to_nov)

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
    ).prefetch_related("scratches")

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
                                   .distinct() \
                                   .prefetch_related("scratches")

    scratched_team_ids = {round_gov.id, round_opp.id}

    def has_team_scratch(judge):
        return any(s.team_id in scratched_team_ids for s in judge.scratches.all())

    excluded_judges = [
        (j.name, j.id, float(j.rank), j.wing_only)
        for j in excluded_judges
        if not has_team_scratch(j)
    ]
    included_judges = [
        (j.name, j.id, float(j.rank), j.wing_only)
        for j in included_judges
        if not has_team_scratch(j)
    ]

    included_judges = sorted(included_judges, key=lambda x: -x[2])
    excluded_judges = sorted(excluded_judges, key=lambda x: -x[2])
    is_outround = True

    context = {
        "round_obj": round_obj,
        "round_gov": round_gov,
        "round_opp": round_opp,
        "current_judge_id": current_judge_id,
        "current_judge_obj": current_judge_obj,
        "current_judge_name": current_judge_name,
        "current_judge_rank": current_judge_rank,
        "var_to_nov": var_to_nov,
        "other_round_num": other_round_num,
        "other_round_type": other_round_type,
        "excluded_judges": excluded_judges,
        "included_judges": included_judges,
        "is_outround": is_outround,
    }
    return render(request, "pairing/judge_dropdown.html", context)


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

    context = {
        "round_obj": round_obj,
        "current_team": current_team,
        "current_team_id": current_team_id,
        "round_id": round_id,
        "breaking_teams_by_type": breaking_teams_by_type,
        "excluded_teams": excluded_teams,
        "included_teams": included_teams,
        "position": position,
        "is_outround": True,
    }

    return render(request, "pairing/team_dropdown.html", context)


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
            "title": f"Entering Ballot for {round_obj}",
            "gov_team": round_obj.gov_team,
            "opp_team": round_obj.opp_team,
        })




def export_outround_pairings_csv_view(request, type_of_round=BreakingTeam.VARSITY):
    return export_pairings_csv(is_outround=True, type_of_round=type_of_round)


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


def update_choice(request, outround_id):
    outround = get_object_or_404(Outround, pk=outround_id)

    outround.choice += 1

    if outround.choice == 3:
        outround.choice = 0

    outround.save()
    data = {
        "success": True,
        "data": f"{outround.get_choice_display()} choice",
    }

    return JsonResponse(data)


def create_forum_view_data(type_of_round):
    outrounds = Outround.objects.exclude(
        victor=Outround.UNKNOWN
    ).filter(
        type_of_round=type_of_round
    )

    rounds = outrounds.values_list("num_teams")
    rounds = [r[0] for r in rounds]
    rounds = list(set(rounds))
    rounds.sort(key=lambda r: r, reverse=True)

    results = []

    for _round in rounds:
        to_add = {}
        to_display = outrounds.filter(num_teams=_round)

        to_add["label"] = f"[{'N' if type_of_round else 'V'}] Ro{_round}"
        to_add["results"] = []

        for outround in to_display:
            loser = outround.loser
            winner = outround.winner
            loser_hybrid = (
                f" / {loser.hybrid_school.name}" if loser.hybrid_school else ""
            )
            winner_hybrid = (
                f" / {winner.hybrid_school.name}" if winner.hybrid_school else ""
            )
            loser_role = "GOV" if loser == outround.gov_team else "OPP"
            winner_role = "GOV" if winner == outround.gov_team else "OPP"
            result_str = (
                f"[{loser.breaking_team.seed}] {loser.display} "
                f"({loser.debaters.first().name}, {loser.debaters.last().name}) "
                f"from {loser.school.name}{loser_hybrid} ({loser_role}) drops to\n"
                f"[{winner.breaking_team.seed}] {winner.display} "
                f"({winner.debaters.first().name}, {winner.debaters.last().name}) "
                f"from {winner.school.name}{winner_hybrid} ({winner_role})"
            )
            to_add["results"].append(result_str)

        results.append(to_add)
    return {
        "rounds": rounds,
        "results": results,
        "type_of_round": type_of_round,
    }


def forum_view(request, type_of_round):
    return render(request,
                  "outrounds/forum_result.html",
                  create_forum_view_data(type_of_round))

def alternative_rooms(request, round_id, current_room_id=None):
    round_obj = Outround.objects.get(id=int(round_id))
    num_teams = round_obj.num_teams

    current_room_obj = None
    if current_room_id is not None:
        try:
            current_room_obj = Room.objects.get(id=int(current_room_id))
        except Room.DoesNotExist:
            pass

    rooms = set(Room.objects.filter(
        roomcheckin__round_number=0
    ).annotate(
        has_round=Exists(Outround.objects.filter(room_id=OuterRef("id"),
                                                 num_teams=num_teams))
    ).order_by("-rank"))

    viable_unpaired_rooms = list(filter(lambda room: not room.has_round, rooms))
    viable_paired_rooms = list(filter(lambda room: room.has_round, rooms))
    return render(request, "pairing/room_dropdown.html", {
        "current_room": current_room_obj,
        "round_obj": round_obj,
        "viable_unpaired_rooms": viable_unpaired_rooms,
        "viable_paired_rooms": viable_paired_rooms
    })

@permission_required("tab.tab_settings.can_change", login_url="/403/")
def assign_judges_to_pairing(request, round_type=Outround.VARSITY):
    valid_round_types = dict(Outround.TYPE_OF_ROUND_CHOICES)
    if round_type not in valid_round_types:
        return redirect_and_flash_error(request, "Invalid round type specified.")
    num_teams = Outround.objects.filter(type_of_round=round_type
                                        ).aggregate(Min("num_teams"))["num_teams__min"]

    if request.method == "POST":
        try:
            type_str = "Varsity" if round_type == Outround.VARSITY else "Novice"
            round_str = f"Round-of-{num_teams}-{type_str}"
            backup.backup_round(round_number=round_str,
                                btype=backup.BEFORE_JUDGE_ASSIGN)
            assign_judges.add_outround_judges(round_type=round_type)
        except Exception:
            emit_current_exception()
            return redirect_and_flash_error(request,
                                            "Got error during judge assignment")
    return redirect(f"/outround_pairing/{round_type}/{num_teams}")
