import random
import math
from urllib.parse import urlencode

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import permission_required
from django.db.models import Q, Exists, OuterRef, Min
from django.shortcuts import redirect, reverse

from mittab.apps.tab.helpers import redirect_and_flash_error, \
    redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs import assign_judges, assign_rooms
from mittab.libs.errors import *
from mittab.apps.tab.forms import OutroundResultEntryForm
import mittab.libs.tab_logic as tab_logic
import mittab.libs.outround_tab_logic as outround_tab_logic
import mittab.libs.backup as backup
from mittab.libs.data_export.pairings_export import export_pairings_csv
from mittab.libs.cacheing.public_cache import (
    invalidate_outround_public_pairings_cache,
)


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
        invalidate_outround_public_pairings_cache(type_of_round)

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


def get_outround_options(var_teams_to_break, nov_teams_to_break):
    outround_options = []

    while not math.log(var_teams_to_break, 2) % 1 == 0:
        var_teams_to_break += 1

    while not math.log(nov_teams_to_break, 2) % 1 == 0:
        nov_teams_to_break += 1

    while var_teams_to_break > 1:
        if Outround.objects.filter(type_of_round=BreakingTeam.VARSITY,
                                   num_teams=var_teams_to_break).exists():
            num_teams = int(var_teams_to_break)
            outround_options.append({
                "type_of_round": BreakingTeam.VARSITY,
                "num_teams": num_teams,
                "label": f"[V] Ro{num_teams}",
                "path": reverse("outround_pairing_view", kwargs={
                    "type_of_round": BreakingTeam.VARSITY,
                    "num_teams": num_teams,
                }),
            })
        var_teams_to_break /= 2

    while nov_teams_to_break > 1:
        if Outround.objects.filter(type_of_round=BreakingTeam.NOVICE,
                                   num_teams=nov_teams_to_break).exists():
            num_teams = int(nov_teams_to_break)
            outround_options.append({
                "type_of_round": BreakingTeam.NOVICE,
                "num_teams": num_teams,
                "label": f"[N] Ro{num_teams}",
                "path": reverse("outround_pairing_view", kwargs={
                    "type_of_round": BreakingTeam.NOVICE,
                    "num_teams": num_teams,
                }),
            })
        nov_teams_to_break /= 2

    return outround_options


def _coerce_selected_num_teams(value):
    if value in (None, "", "none"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _available_round_map(outround_options):
    result = {Outround.VARSITY: set(), Outround.NOVICE: set()}
    for option in outround_options:
        result[option["type_of_round"]].add(option["num_teams"])
    return result


def _selected_round_specs(request, outround_options, fallback_spec):
    available_map = _available_round_map(outround_options)
    selected_varsity = _coerce_selected_num_teams(
        request.GET.get("select_varsity")
    )
    selected_novice = _coerce_selected_num_teams(
        request.GET.get("select_novice")
    )

    if selected_varsity is None and selected_novice is None:
        # Default to the furthest-along available round in each division.
        # (furthest along => smallest remaining bracket size)
        if available_map[Outround.VARSITY]:
            selected_varsity = min(available_map[Outround.VARSITY])
        if available_map[Outround.NOVICE]:
            selected_novice = min(available_map[Outround.NOVICE])
        if selected_varsity is None and selected_novice is None and fallback_spec:
            fallback_type, fallback_num = fallback_spec
            if fallback_type == Outround.VARSITY:
                selected_varsity = fallback_num
            elif fallback_type == Outround.NOVICE:
                selected_novice = fallback_num

    if selected_varsity not in available_map[Outround.VARSITY]:
        selected_varsity = None
    if selected_novice not in available_map[Outround.NOVICE]:
        selected_novice = None

    selected_specs = []
    if selected_varsity is not None:
        selected_specs.append((Outround.VARSITY, selected_varsity))
    if selected_novice is not None:
        selected_specs.append((Outround.NOVICE, selected_novice))
    if not selected_specs and fallback_spec:
        selected_specs = [fallback_spec]

    return selected_specs, selected_varsity, selected_novice


def _selected_round_specs_from_post(request):
    selected_varsity = _coerce_selected_num_teams(
        request.POST.get("selected_varsity")
    )
    selected_novice = _coerce_selected_num_teams(
        request.POST.get("selected_novice")
    )
    selected_specs = []
    if selected_varsity is not None:
        selected_specs.append((Outround.VARSITY, selected_varsity))
    if selected_novice is not None:
        selected_specs.append((Outround.NOVICE, selected_novice))
    return selected_specs


def _selected_round_specs_from_get(request, fallback_spec=None):
    selected_varsity = _coerce_selected_num_teams(
        request.GET.get("select_varsity")
    )
    selected_novice = _coerce_selected_num_teams(
        request.GET.get("select_novice")
    )
    selected_specs = []
    if selected_varsity is not None and Outround.objects.filter(
            type_of_round=Outround.VARSITY, num_teams=selected_varsity).exists():
        selected_specs.append((Outround.VARSITY, selected_varsity))
    if selected_novice is not None and Outround.objects.filter(
            type_of_round=Outround.NOVICE, num_teams=selected_novice).exists():
        selected_specs.append((Outround.NOVICE, selected_novice))
    if not selected_specs:
        # Match default page behavior: pick furthest-along active rounds by type.
        varsity_num = Outround.objects.filter(
            type_of_round=Outround.VARSITY
        ).aggregate(Min("num_teams"))["num_teams__min"]
        novice_num = Outround.objects.filter(
            type_of_round=Outround.NOVICE
        ).aggregate(Min("num_teams"))["num_teams__min"]
        if varsity_num is not None:
            selected_specs.append((Outround.VARSITY, int(varsity_num)))
        if novice_num is not None:
            selected_specs.append((Outround.NOVICE, int(novice_num)))
        if not selected_specs and fallback_spec:
            selected_specs = [fallback_spec]
    return selected_specs


def _selected_scope_filter(
        selected_specs,
        relation_name="judges_outrounds",
        undecided_only=False):
    scope_filter = Q()
    for round_type, num_teams in selected_specs:
        clause = {
            f"{relation_name}__type_of_round": round_type,
            f"{relation_name}__num_teams": num_teams,
        }
        if undecided_only:
            clause[f"{relation_name}__victor"] = Outround.UNKNOWN
        scope_filter |= Q(**clause)
    return scope_filter


def _existing_selected_specs(selected_specs):
    existing = []
    for round_type, num_teams in selected_specs:
        if Outround.objects.filter(
            type_of_round=round_type,
            num_teams=num_teams,
        ).exists():
            existing.append((round_type, num_teams))
    return existing


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

    nov_teams_to_break = TabSettings.get("nov_teams_to_break")
    var_teams_to_break = TabSettings.get("var_teams_to_break")

    if not nov_teams_to_break or not var_teams_to_break:
        return redirect_and_flash_error(request,
                                        "Please check your break tab settings",
                                        path="/")

    outround_options = get_outround_options(var_teams_to_break,
                                            nov_teams_to_break)

    selected_specs, selected_varsity, selected_novice = _selected_round_specs(
        request,
        outround_options,
        fallback_spec=(type_of_round, num_teams),
    )

    selected_query = {}
    if selected_varsity is not None:
        selected_query["select_varsity"] = selected_varsity
    if selected_novice is not None:
        selected_query["select_novice"] = selected_novice
    query_string = urlencode(selected_query)
    for option in outround_options:
        option["url"] = (
            f"{option['path']}?{query_string}"
            if query_string
            else option["path"]
        )

    lost_outrounds = [t.loser.id for t in Outround.objects.all() if t.loser]

    outround_sections = []
    excluded_teams_map = {}
    for selected_type, selected_num in selected_specs:
        section_outrounds = list(
            Outround.objects.filter(
                type_of_round=selected_type,
                num_teams=selected_num
            ).prefetch_related(
                "room__tags",
                "gov_team__required_room_tags",
                "opp_team__required_room_tags",
                "judges__required_room_tags",
            )
        )
        judges_per_panel = (
            TabSettings.get("var_panel_size", 3)
            if selected_type == BreakingTeam.VARSITY
            else TabSettings.get("nov_panel_size", 3)
        )
        judge_slots = [i for i in range(1, judges_per_panel + 1)]
        section_label = f"[{'V' if selected_type == BreakingTeam.VARSITY else 'N'}] Ro{selected_num}"
        pairing_released = (
            TabSettings.get("var_teams_visible", 256) <= selected_num
            if selected_type == BreakingTeam.VARSITY
            else TabSettings.get("nov_teams_visible", 256) <= selected_num
        )
        outround_sections.append({
            "type_of_round": selected_type,
            "num_teams": selected_num,
            "label": section_label,
            "outrounds": section_outrounds,
            "judges_per_panel": judges_per_panel,
            "judge_slots": judge_slots,
            "pairing_exists": len(section_outrounds) > 0,
            "pairing_released": pairing_released,
        })

        section_excluded_teams = BreakingTeam.objects.filter(
            type_of_team=selected_type
        ).exclude(
            team__id__in=lost_outrounds
        )
        section_excluded_teams = [t.team for t in section_excluded_teams]
        section_excluded_teams = [
            t for t in section_excluded_teams
            if not Outround.objects.filter(
                type_of_round=selected_type,
                num_teams=selected_num,
                gov_team=t
            ).exists()
            and not Outround.objects.filter(
                type_of_round=selected_type,
                num_teams=selected_num,
                opp_team=t
            ).exists()
        ]
        for team in section_excluded_teams:
            excluded_teams_map[team.id] = team
    excluded_teams = list(excluded_teams_map.values())

    page_label = "Selected Outrounds"
    if len(outround_sections) == 1:
        page_label = outround_sections[0]["label"]
    control_section = outround_sections[0] if len(outround_sections) == 1 else None
    control_round_type = control_section["type_of_round"] if control_section else None
    control_num_teams = control_section["num_teams"] if control_section else None

    warnings = []
    for section in outround_sections:
        for pairing in section["outrounds"]:
            if pairing.room is None:
                continue

            required_tags = assign_rooms.get_required_tags(pairing)
            actual_tags = set(pairing.room.tags.all())

            if required_tags <= actual_tags:
                continue

            missing_tags = required_tags - actual_tags
            plural = "s" if len(missing_tags) > 1 else ""
            missing_tags_str = ", ".join(str(tag) for tag in missing_tags)
            warnings.append(
                f"{section['label']}: {pairing.gov_team} vs {pairing.opp_team} "
                f"requires tag{plural} {missing_tags_str} "
                f"that are not assigned to room {pairing.room}"
            )

    selected_judge_scope = _selected_scope_filter(
        selected_specs,
        "judges_outrounds",
        undecided_only=True,
    )
    if selected_specs:
        excluded_judges = Judge.objects.exclude(selected_judge_scope).filter(
            checkin__round_number=0
        ).distinct()
        non_checkins = Judge.objects.exclude(selected_judge_scope).exclude(
            checkin__round_number=0
        ).distinct()
    else:
        excluded_judges = Judge.objects.filter(checkin__round_number=0).distinct()
        non_checkins = Judge.objects.exclude(checkin__round_number=0).distinct()

    selected_room_scope = _selected_scope_filter(
        selected_specs,
        "rooms_outrounds",
        undecided_only=True,
    )
    available_rooms = Room.objects.filter(roomcheckin__round_number=0)
    if selected_specs:
        available_rooms = available_rooms.exclude(selected_room_scope)
    available_rooms = available_rooms.distinct()

    size = max(1, max(list(
        map(
            len,
            [excluded_teams, excluded_judges, non_checkins, available_rooms]
        ))))
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
        "pairing_released": control_section["pairing_released"] if control_section else False,
        "label": page_label,
        "outround_options": outround_options,
        "outrounds": control_section["outrounds"] if control_section else [],
        "judges_per_panel": control_section["judges_per_panel"] if control_section else 0,
        "judge_slots": control_section["judge_slots"] if control_section else [],
        "pairing_exists": control_section["pairing_exists"] if control_section else False,
        "outround_sections": outround_sections,
        "control_section": control_section,
        "control_round_type": control_round_type,
        "control_num_teams": control_num_teams,
        "excluded_teams": excluded_teams,
        "excluded_judges": excluded_judges,
        "non_checkins": non_checkins,
        "available_rooms": available_rooms,
        "size": size,
        "warning": warning,
        "warnings": warnings,
        "excluded_people": excluded_people,
        "selected_varsity": selected_varsity,
        "selected_novice": selected_novice,
        "selected_specs": selected_specs,
        "available_varsity_rounds": sorted(
            [o for o in outround_options if o["type_of_round"] == Outround.VARSITY],
            key=lambda x: x["num_teams"],
            reverse=True,
        ),
        "available_novice_rounds": sorted(
            [o for o in outround_options if o["type_of_round"] == Outround.NOVICE],
            key=lambda x: x["num_teams"],
            reverse=True,
        ),
        "stats_round_numbers": list(dict.fromkeys([spec[1] for spec in selected_specs])),
        "return_path": request.get_full_path(),
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
        current_judge_obj = Judge.objects.prefetch_related("scratches", "schools").get(
            id=current_judge_id
        )
        current_judge_name = current_judge_obj.name
        current_judge_rank = current_judge_obj.rank
    except TypeError:
        current_judge_id, current_judge_obj, current_judge_rank = "", "", ""
        current_judge_name = "No judge"

    selected_specs = _selected_round_specs_from_get(
        request,
        fallback_spec=(round_obj.type_of_round, round_obj.num_teams),
    )

    scope_filter = _selected_scope_filter(
        selected_specs,
        "judges_outrounds",
        undecided_only=True,
    )
    checked_in_judges = Judge.objects.filter(
        checkin__round_number=0
    ).prefetch_related("scratches", "schools", "judges").distinct()
    if selected_specs:
        included_judges = checked_in_judges.filter(scope_filter).distinct()
        occupied_in_scope = included_judges.exclude(
            judges_outrounds=round_obj
        )
        excluded_judges = checked_in_judges.exclude(
            id__in=occupied_in_scope.values_list("id", flat=True)
        )
    else:
        excluded_judges = checked_in_judges
        included_judges = checked_in_judges.filter(judges_outrounds=round_obj).distinct()

    excluded_judges = excluded_judges.exclude(judges_outrounds=round_obj).distinct()

    eligible_excluded = assign_judges.can_judge_teams(
        excluded_judges,
        round_gov,
        round_opp,
        allow_rejudges=True,
    )
    eligible_included = assign_judges.can_judge_teams(
        included_judges,
        round_gov,
        round_opp,
        allow_rejudges=True,
    )

    excluded_judges = [
        (j.name, j.id, float(j.rank), j.wing_only)
        for j in eligible_excluded
    ]
    included_judges = [
        (j.name, j.id, float(j.rank), j.wing_only)
        for j in eligible_included
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
def switch_sides(request, round_id):
    try:
        round_obj = Outround.objects.select_related("gov_team",
                                                    "opp_team").get(id=int(round_id))
        if not round_obj.gov_team or not round_obj.opp_team:
            return JsonResponse({"success": False})
        round_obj.gov_team, round_obj.opp_team = round_obj.opp_team, round_obj.gov_team
        if round_obj.choice == Outround.GOV:
            round_obj.choice = Outround.OPP
        elif round_obj.choice == Outround.OPP:
            round_obj.choice = Outround.GOV
        round_obj.save()
        data = {
            "success": True,
            "round_id": round_obj.id,
            "gov_team": {
                "id": round_obj.gov_team.id,
                "name": round_obj.gov_team.name
            },
            "opp_team": {
                "id": round_obj.opp_team.id,
                "name": round_obj.opp_team.name
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

    invalidate_outround_public_pairings_cache(type_of_round)

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
    round_obj = Outround.objects.prefetch_related(
        "gov_team__required_room_tags",
        "opp_team__required_room_tags",
        "judges__required_room_tags",
    ).get(id=int(round_id))

    current_room_obj = None
    if current_room_id is not None:
        try:
            current_room_obj = Room.objects.get(id=int(current_room_id))
        except Room.DoesNotExist:
            pass

    selected_specs = _selected_round_specs_from_get(
        request,
        fallback_spec=(round_obj.type_of_round, round_obj.num_teams),
    )
    selected_round_filter = Q()
    for round_type, num_teams in selected_specs:
        selected_round_filter |= Q(
            type_of_round=round_type,
            num_teams=num_teams,
            victor=Outround.UNKNOWN,
        )

    required_tags = assign_rooms.get_required_tags(round_obj)

    rooms = Room.objects.filter(
        roomcheckin__round_number=0
    ).annotate(
        has_round=Exists(
            Outround.objects.filter(selected_round_filter)
            .exclude(id=round_obj.id)
            .filter(room_id=OuterRef("id"))
        )
    ).order_by("-rank").prefetch_related("tags")

    viable_rooms = [
        room for room in rooms
        if set(room.tags.all()).issuperset(required_tags)
    ]

    viable_unpaired_rooms = [room for room in viable_rooms if not room.has_round]
    viable_paired_rooms = [room for room in viable_rooms if room.has_round]
    return render(request, "pairing/room_dropdown.html", {
        "current_room": current_room_obj,
        "round_obj": round_obj,
        "viable_unpaired_rooms": viable_unpaired_rooms,
        "viable_paired_rooms": viable_paired_rooms
    })

@permission_required("tab.tab_settings.can_change", login_url="/403/")
def assign_judges_to_pairing(request, round_type=None):
    if request.method != "POST":
        return redirect_and_flash_error(request, "Invalid request method.")

    selected_specs = _selected_round_specs_from_post(request)
    if not selected_specs and round_type is not None:
        num_teams = Outround.objects.filter(type_of_round=round_type).aggregate(
            Min("num_teams")
        )["num_teams__min"]
        if num_teams is not None:
            selected_specs = [(round_type, int(num_teams))]

    selected_specs = _existing_selected_specs(selected_specs)
    if not selected_specs:
        return redirect_and_flash_error(
            request, "No valid outround scope selected for judge assignment."
        )

    try:
        scope_label = ", ".join(
            f"Ro{num_teams}-{'Varsity' if round_type == Outround.VARSITY else 'Novice'}"
            for round_type, num_teams in selected_specs
        )
        backup.backup_round(
            round_number=scope_label,
            btype=backup.BEFORE_JUDGE_ASSIGN
        )
        assign_judges.add_outround_judges(round_specs=selected_specs)
    except JudgeAssignmentError as e:
        return redirect_and_flash_error(request, str(e).replace("'", ""))
    except Exception:
        emit_current_exception()
        return redirect_and_flash_error(request,
                                        "Got error during judge assignment")

    return redirect_and_flash_success(request, "Outround judges assigned successfully.")


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def assign_rooms_to_pairing(request):
    if request.method != "POST":
        return redirect_and_flash_error(request, "Invalid request method.")

    selected_specs = _existing_selected_specs(
        _selected_round_specs_from_post(request)
    )
    if not selected_specs:
        return redirect_and_flash_error(
            request, "No valid outround scope selected for room assignment."
        )

    try:
        scope_label = ", ".join(
            f"Ro{num_teams}-{'Varsity' if round_type == Outround.VARSITY else 'Novice'}"
            for round_type, num_teams in selected_specs
        )
        backup.backup_round(
            round_number=scope_label,
            btype=backup.BEFORE_ROOM_ASSIGN
        )
        assign_rooms.add_outround_rooms(round_specs=selected_specs)
    except RoomAssignmentError as e:
        return redirect_and_flash_error(request, str(e).replace("'", ""))
    except Exception:
        emit_current_exception()
        return redirect_and_flash_error(request,
                                        "Got error during room assignment")

    return redirect_and_flash_success(request, "Outround rooms assigned successfully.")
