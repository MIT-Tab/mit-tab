# pylint: disable=too-many-lines
import random
import datetime
import os

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import logout
from django.contrib.auth.decorators import permission_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.shortcuts import redirect

from mittab.apps.tab.helpers import redirect_and_flash_error, \
    redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs import assign_rooms
from mittab.libs.errors import *
from mittab.apps.tab.forms import BackupForm, ResultEntryForm, \
    UploadBackupForm, score_panel, \
    validate_panel, EBallotForm

import mittab.libs.cacheing.cache_logic as cache_logic
from mittab.libs.data_export.pairings_export import export_pairings_csv
import mittab.libs.tab_logic as tab_logic
import mittab.libs.assign_judges as assign_judges
from mittab.libs.assign_judges import judge_team_rejudge_counts
import mittab.libs.backup as backup
from mittab.libs.cacheing.public_cache import (
    invalidate_inround_public_pairings_cache,
    invalidate_public_rankings_cache,
)


def invalidate_public_ballot_cache():
    cache_logic.invalidate_cache("public_ballots_last")
    cache_logic.invalidate_cache("public_ballots_all")
    invalidate_public_rankings_cache()


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def pair_round(request):
    cache_logic.clear_cache()
    current_round = TabSettings.objects.get(key="cur_round")
    current_round_number = current_round.value
    if request.method == "POST":
        # We should pair the round
        try:
            TabSettings.set("pairing_released", 0)
            backup.backup_round(btype=backup.BEFORE_PAIRING)

            with transaction.atomic():
                tab_logic.pair_round()
                invalidate_inround_public_pairings_cache()
                current_round.value = current_round.value + 1
                current_round.save()
                latest_release_round = max(current_round.value - 2, 0)
                TabSettings.set("latest_ballots_released", latest_release_round)
                invalidate_public_ballot_cache()
        except Exception as exp:
            emit_current_exception()
            return redirect_and_flash_error(
                request,
                f"Could not pair next round, got error: {exp}")
        return view_status(request)
    else:
        # See if we can pair the round
        title = f"Pairing Round {current_round_number}"
        check_status = []

        judges = tab_logic.have_enough_judges(current_round_number)
        rooms = tab_logic.have_enough_rooms(current_round_number)

        msg = (
            f"N/2 Judges checked in for Round {current_round_number}? "
            f"Need {judges[1][1]}, have {judges[1][0]}"
        )
        if judges[0]:
            check_status.append((msg, "Yes", "Judges are checked in"))
        else:
            check_status.append((msg, "No", "Not enough judges"))

        msg = (
            f"N/2 Rooms available Round {current_round_number}? "
            f"Need {rooms[1][1]}, have {rooms[1][0]}"
        )
        if rooms[0]:
            check_status.append((msg, "Yes", "Rooms are checked in"))
        else:
            check_status.append((msg, "No", "Not enough rooms"))

        msg = f"All Rounds properly entered for Round {current_round_number - 1}"
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

        context = {
            "title": title,
            "check_status": check_status,
            "judges": judges,
            "rooms": rooms,
            "ready_to_pair": ready_to_pair,
            "ready_to_pair_alt": ready_to_pair_alt,
            "current_round_number": current_round_number,
            "current_round": current_round,
        }

        return render(request, "pairing/pair_round.html", context)


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def re_pair_round(request):
    """
    Re-pair the current round by clearing existing pairings and
    re-running the pairing algorithm.
    This is a safe operation that:
    1. Backs up the current state
    2. Clears Round, Bye, and NoShow objects for the current round
    3. Decrements cur_round temporarily
    4. Re-runs the pairing algorithm which will re-increment cur_round
    """
    cache_logic.clear_cache()
    current_round_obj = TabSettings.objects.get(key="cur_round")
    current_round_number = current_round_obj.value - 1

    if current_round_number < 1:
        return redirect_and_flash_error(
            request,
            "No round has been paired yet to re-pair"
        )

    if request.method == "POST":
        try:
            backup_name = (
                f"round_{current_round_number}_before_repairing"
            )
            backup.backup_round(
                btype=backup.OTHER,
                round_number=current_round_number,
                name=backup_name,
            )

            with transaction.atomic():
                tab_logic.clear_current_round_pairing()

                # Decrement cur_round so pair_round() will pair for same round
                current_round_obj.value = current_round_number
                current_round_obj.save()

                # Re-run the pairing algorithm
                tab_logic.pair_round()
                current_round_obj.value = current_round_obj.value + 1
                current_round_obj.save()

            # Add success message and redirect to view_status
            messages.add_message(
                request,
                messages.SUCCESS,
                f"Successfully re-paired round {current_round_number}"
            )
            return view_status(request)
        except Exception as exp:
            emit_current_exception()
            return redirect_and_flash_error(
                request,
                f"Could not re-pair round, got error: {exp}"
            )

    return render(request, "pairing/confirm_re_pair.html", {
        "round_number": current_round_number,
    })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def assign_judges_to_pairing(request):
    current_round_number = TabSettings.objects.get(key="cur_round").value - 1
    if request.method == "POST":
        try:
            backup.backup_round(
                round_number=current_round_number,
                btype=backup.BEFORE_JUDGE_ASSIGN)
            assign_judges.add_judges()
        except JudgeAssignmentError as e:
            return redirect_and_flash_error(request, str(e).replace("'", ""))
        except Exception:
            emit_current_exception()
            return redirect_and_flash_error(request,
                                            "Got error during judge assignment")
    return redirect("/pairings/status/")


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def assign_rooms_to_pairing(request):
    current_round_number = TabSettings.objects.get(key="cur_round").value - 1
    if request.method == "POST":
        try:
            backup.backup_round(
                round_number=current_round_number,
                btype=backup.BEFORE_ROOM_ASSIGN)
            assign_rooms.add_rooms()
        except Exception:
            emit_current_exception()
            return redirect_and_flash_error(request,
                                            "Got error during room assignment")
    return redirect("/pairings/status/")


def alternative_rooms(request, round_id, current_room_id=None):
    round_obj = Round.objects.prefetch_related(
        "gov_team__required_room_tags",
        "opp_team__required_room_tags",
        "judges__required_room_tags"
    ).get(id=int(round_id))
    round_number = round_obj.round_number

    current_room_obj = None
    if current_room_id is not None:
        try:
            current_room_obj = Room.objects.get(id=int(current_room_id))
        except Room.DoesNotExist:
            pass

    # Fetch all rooms checked in for the given round, ordered by rank
    rooms = Room.objects.filter(
        roomcheckin__round_number=round_number
    ).annotate(
        has_round=Exists(Round.objects.filter(room_id=OuterRef("id")))
    ).order_by("-rank").prefetch_related("tags")

    required_tags = assign_rooms.get_required_tags(round_obj)

    viable_rooms = set(room for room in rooms if
                       set(room.tags.all()).issuperset(required_tags))

    viable_unpaired_rooms = list(filter(lambda room: not room.has_round, viable_rooms))
    viable_paired_rooms = list(filter(lambda room: room.has_round, viable_rooms))
    return render(request, "pairing/room_dropdown.html", {
        "current_room": current_room_obj,
        "round_obj": round_obj,
        "viable_unpaired_rooms": viable_unpaired_rooms,
        "viable_paired_rooms": viable_paired_rooms,
    })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def assign_room(request, round_id, new_room_id, outround=False):
    try:
        if outround:
            round_obj = Outround.objects.get(id=int(round_id))
        else:
            round_obj = Round.objects.get(id=int(round_id))
        room_obj = Room.objects.get(id=int(new_room_id))
        round_obj.room = room_obj
        round_obj.save()
        data = {
            "success": True,
            "room_id": room_obj.id,
            "round_id": round_obj.id,
            "room_name": room_obj.name,
        }
    except Exception:
        emit_current_exception()
        data = {"success": False}
        return JsonResponse(data, status=400)
    return JsonResponse(data)


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def view_backup(request, filename):
    metadata = backup.get_metadata(filename)
    # metadata format: [filename, name, type, round_num, timestamp, scratches]
    name = metadata[1] if len(metadata) > 1 else "Unknown"
    title = f"Viewing Backup: {name}"
    links = [
        (f"/backup/download/{filename}/", "Download Backup"),
        (f"/backup/restore/{filename}/", "Restore From Backup"),
    ]
    context = {
        "metadata": metadata,
        "name": name,
        "title": title,
        "links": links,
        "filename": filename,
    }
    return render(request, "common/list_data.html", context)


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def download_backup(request, key):
    print(f"Trying to download {key}")
    data = backup.get_backup_content(key)
    response = HttpResponse(data, content_type="text/plain")
    filename = key.split("_")[0]
    response["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def upload_backup(request):
    if request.method == "POST":
        form = UploadBackupForm(request.POST, request.FILES)
        if form.is_valid():
            backup.upload_backup(request.FILES["file"])
            uploaded_name = request.FILES["file"].name
            return redirect_and_flash_success(
                request,
                f"Backup {uploaded_name} uploaded successfully",
                path="/pairing/view_backups/"
            )
        else:
            return redirect_and_flash_error(
                request, "Error uploading backup",
                path="/pairing/view_backups/")
    return redirect("/pairing/view_backups/")


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def manual_backup(request):
    if request.method != "POST":
        return redirect("view_backups")

    form = BackupForm(request.POST)
    if not form.is_valid():
        return redirect_and_flash_error(
            request,
            "Error creating backup: invalid submission.",
            path="/pairing/view_backups/"
        )

    backup_name = form.cleaned_data["backup_name"]
    include_scratches = form.cleaned_data["include_scratches"]

    try:
        backup.backup_round(
            name=backup_name,
            btype=backup.MANUAL,
            include_scratches=include_scratches
        )
    except Exception:
        emit_current_exception()
        return redirect_and_flash_error(
            request,
            "Error creating backup",
            path="/pairing/view_backups/"
        )

    cur_round = TabSettings.objects.get(key="cur_round").value
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
    message = (
        f"Backup {backup_name} created for round {cur_round} at {timestamp}"
    )
    return redirect_and_flash_success(
        request,
        message,
        path="/pairing/view_backups/"
    )


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def view_backups(request):
    backups = backup.list_backups()
    backups.sort(key=lambda x: x[3])

    types = sorted(set(b[2] for b in backups if b[2] != "Unknown"))

    round_values = {str(b[3]) for b in backups}
    rounds = sorted(
        round_values,
        key=lambda value: (
            not value.isdigit(),
            int(value) if value.isdigit() else value.lower()
        )
    )

    create_form = BackupForm()
    upload_form = UploadBackupForm()

    headers = ["Name", "Type", "Round", "Timestamp", "Scratches"]

    filters = [
        {"id": "type", "label": "Type", "options": types},
        {"id": "round", "label": "Round", "options": rounds},
        {"id": "scratches", "label": "Scratches", "options": ["Yes", "No", "Unknown"]},
    ]

    return render(request, "tab/backup_list.html", {
        "backups": backups,
        "create_form": create_form,
        "upload_form": upload_form,
        "headers": headers,
        "title": "Backup List",
        "filters": filters,
    })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def restore_backup(request, filename):
    backup.restore_from_backup(filename)
    logout(request)
    return redirect_and_flash_success(
        request,
        "Restored from backup. You have been logged out as a result.",
        path="/")


def view_status(request):
    current_round_number = TabSettings.get("cur_round") - 1
    return view_round(request, current_round_number)


def view_round(request, round_number):
    errors, excluded_teams = [], []

    tot_rounds = TabSettings.get("tot_rounds", 5)

    round_pairing = tab_logic.sorted_pairings(
        round_number
    )
    warnings = []
    for pairing in round_pairing:
        if pairing.room is None:
            continue
        required_tags = assign_rooms.get_required_tags(pairing)
        actual_tags = set(pairing.room.tags.all())

        if not required_tags <= actual_tags:
            missing_tags = required_tags - actual_tags
            plural = "s" if len(missing_tags) > 1 else ""
            missing_tags_str = ", ".join(str(tag) for tag in missing_tags)

            warnings.append(
                f"{pairing.gov_team} vs {pairing.opp_team} "
                f"requires tag{plural} {missing_tags_str} "
                f"that are not assigned to room {pairing.room}"
            )

    # For the template since we can't pass in something nicer like a hash
    round_info = [pair for pair in round_pairing]
    round_ids = [pair.id for pair in round_info]
    manual_judge_assignments = {}
    if round_ids:
        manual_entries = ManualJudgeAssignment.objects.filter(
            round_id__in=round_ids
        ).values_list("round_id", "judge_id")
        for round_id, judge_id in manual_entries:
            manual_judge_assignments.setdefault(round_id, set()).add(judge_id)

    all_judges_in_round = [j for pairing in round_info for j in pairing.judges.all()]

    if all_judges_in_round:
        all_teams = []
        for pairing in round_info:
            all_teams.extend([pairing.gov_team, pairing.opp_team])

        # Make single batch call instead of nested loops (fixes N+1 issue)
        display_counts = judge_team_rejudge_counts(all_judges_in_round, all_teams)

        judge_rejudge_counts = {}
        for judge in all_judges_in_round:
            judge_rejudge_counts[judge.id] = {}
            for pairing in round_info:
                total_rejudges = 0
                if judge.id in display_counts and display_counts[judge.id]:
                    gov_count = display_counts[judge.id].get(pairing.gov_team.id, 0)
                    opp_count = display_counts[judge.id].get(pairing.opp_team.id, 0)
                    total_rejudges = max(gov_count, opp_count)
                judge_rejudge_counts[judge.id][pairing.id] = (
                    total_rejudges if total_rejudges > 0 else None
                )
    else:
        judge_rejudge_counts = {}

    paired_teams = [team.gov_team for team in round_pairing
                    ] + [team.opp_team for team in round_pairing]
    n_over_two = Team.objects.filter(checked_in=True).count() / 2

    for present_team in Team.objects.filter(checked_in=True):
        if present_team not in paired_teams:
            excluded_teams.append(present_team)

    excluded_teams_no_bye = [team for team in excluded_teams
                             if not Bye.objects.filter(round_number=round_number,
                                                       bye_team=team).exists()]
    num_excluded = len(excluded_teams_no_bye)
    simulate_round_button = os.environ.get("MITTAB_ENV") in (
        "development", "test-deployment"
    )
    pairing_exists = len(round_pairing) > 0
    pairing_released = TabSettings.get("pairing_released", 0) == 1
    judges_assigned = all((r.judges.count() > 0 for r in round_info))
    rooms_assigned = all((r.room is not None for r in round_info))
    outstanding_ballots = Round.objects.filter(
        round_number=round_number,
        victor=Round.NONE
    ).exists()
    all_ballots_in = pairing_exists and not outstanding_ballots
    latest_ballots_released = int(
        TabSettings.get("latest_ballots_released", 0) or 0
    )
    current_round_ballots_released = (
        round_number > 0 and latest_ballots_released >= round_number
    )
    excluded_judges = Judge.objects.exclude(
        judges__round_number=round_number).filter(
            checkin__round_number=round_number)
    non_checkins = Judge.objects.exclude(
        judges__round_number=round_number).exclude(
            checkin__round_number=round_number)
    available_rooms = Room.objects.exclude(
        round__round_number=round_number).exclude(rank=0)
    size = max(list(map(len, [excluded_judges, non_checkins, excluded_teams])))
    # The minimum rank you want to warn on
    warning = 5
    judge_slots = [1, 2, 3]

    # A seemingly complex one liner to do a fairly simple thing
    # Generates a nested list like:
    # [ Byes ][Judges not in round][Judges not in round]
    # [ Team1][     CJudge1       ][       Judge1      ]
    # [ Team2][     CJudge2       ][       Judge2      ]
    # [      ][     CJudge3       ][       Judge3      ]
    # [      ][                   ][       Judge4      ]
    excluded_people = list(
        zip(*[
            x + [""] * (size - len(x)) for x in [
                list(excluded_teams),
                list(excluded_judges),
                list(non_checkins),
                list(available_rooms)
            ]
        ]))

    if round_number > 0:
        ballot_release_button_text = (
            f"Stop showing round {round_number} ballots"
            if current_round_ballots_released else
            f"Show round {round_number} ballots"
        )
    else:
        ballot_release_button_text = "Show ballots"

    context = {
        "errors": errors,
        "excluded_teams": excluded_teams,
        "tot_rounds": tot_rounds,
        "round_pairing": round_pairing,
        "warnings": warnings,
        "round_info": round_info,
        "all_judges_in_round": all_judges_in_round,
        "judge_rejudge_counts": judge_rejudge_counts,
        "round_number": round_number,
        "all_ballots_in": all_ballots_in,
        "latest_ballots_released": latest_ballots_released,
        "current_round_ballots_released": current_round_ballots_released,
        "ballot_release_button_text": ballot_release_button_text,
        "excluded_teams_no_bye": excluded_teams_no_bye,
        "num_excluded": num_excluded,
        "simulate_round_button": simulate_round_button,
        "pairing_exists": pairing_exists,
        "pairing_released": pairing_released,
        "judges_assigned": judges_assigned,
        "rooms_assigned": rooms_assigned,
        "excluded_judges": excluded_judges,
        "non_checkins": non_checkins,
        "available_rooms": available_rooms,
        "size": size,
        "warning": warning,
        "judge_slots": judge_slots,
        "excluded_people": excluded_people,
        "n_over_two": n_over_two,
        "paired_teams": paired_teams,
        "manual_judge_assignments": manual_judge_assignments,
    }
    return render(request, "pairing/pairing_control.html", context)


def alternative_judges(request, round_id, judge_id=None):
    round_obj = Round.objects.get(id=int(round_id))
    round_number = round_obj.round_number
    round_gov, round_opp = round_obj.gov_team, round_obj.opp_team
    excluded_judges = Judge.objects.exclude(judges__round_number=round_number) \
                                   .filter(checkin__round_number=round_number) \
                                   .prefetch_related("judges", "scratches", "schools")
    included_judges = Judge.objects.filter(judges__round_number=round_number) \
                                   .filter(checkin__round_number=round_number) \
                                   .prefetch_related("judges", "scratches", "schools")

    excluded_judges_list = assign_judges.can_judge_teams(
        excluded_judges, round_gov, round_opp)
    included_judges_list = assign_judges.can_judge_teams(
        included_judges, round_gov, round_opp)

    current_judge_obj = None
    try:
        current_judge_id = int(judge_id)
        current_judge_obj = Judge.objects.prefetch_related(
            "judges", "scratches", "schools").get(id=current_judge_id)
        current_judge_name = current_judge_obj.name
        current_judge_rank = current_judge_obj.rank
    except TypeError:
        current_judge_id, current_judge_rank = "", ""
        current_judge_name = "No judge"

    all_judges = list(excluded_judges_list) + list(included_judges_list)
    if current_judge_obj:
        all_judges.append(current_judge_obj)

    display_counts = judge_team_rejudge_counts(
        all_judges, [round_gov, round_opp], exclude_round_id=round_obj.id
    )

    rejudge_display_counts = {}
    for judge_id, judge_counts in display_counts.items():
        if judge_counts:
            total_rejudges = max(judge_counts.values()) + 1
            rejudge_display_counts[judge_id] = (
                total_rejudges if total_rejudges > 0 else None
            )
        else:
            rejudge_display_counts[judge_id] = None

    current_judge_rejudge_display = (
        rejudge_display_counts.get(current_judge_obj.id) if current_judge_obj else None
    )

    excluded_judges = [
        (j.name, j.id, float(j.rank), rejudge_display_counts.get(j.id), j.wing_only)
        for j in excluded_judges_list
    ]
    included_judges = [
        (j.name, j.id, float(j.rank), rejudge_display_counts.get(j.id), j.wing_only)
        for j in included_judges_list
    ]
    included_judges = sorted(included_judges, key=lambda x: -x[2])
    excluded_judges = sorted(excluded_judges, key=lambda x: -x[2])

    context = {
        "round_obj": round_obj,
        "round_number": round_number,
        "round_gov": round_gov,
        "round_opp": round_opp,
        "excluded_judges": excluded_judges,
        "included_judges": included_judges,
        "current_judge_obj": current_judge_obj,
        "current_judge_id": current_judge_id,
        "current_judge_rank": current_judge_rank,
        "current_judge_name": current_judge_name,
        "current_judge_rejudge_display": current_judge_rejudge_display,
        "rejudge_display_counts": rejudge_display_counts,
        "display_counts": display_counts,
        "all_judges": all_judges,
    }
    return render(request, "pairing/judge_dropdown.html", context)


def alternative_teams(request, round_id, current_team_id, position):
    round_obj = Round.objects.get(pk=round_id)
    current_team = Team.objects.get(pk=current_team_id)
    round_number = round_obj.round_number
    excluded_teams = Team.objects.exclude(gov_team__round_number=round_number) \
        .exclude(opp_team__round_number=round_number) \
        .exclude(pk=current_team_id)
    included_teams = Team.objects.exclude(pk__in=excluded_teams) \
        .exclude(pk=current_team_id)
    context = {
        "round_obj": round_obj,
        "current_team": current_team,
        "current_team_id": current_team_id,
        "round_number": round_number,
        "round_id": round_id,
        "excluded_teams": excluded_teams,
        "included_teams": included_teams,
        "position": position,
        "is_outround": False,
    }
    return render(request, "pairing/team_dropdown.html", context)


def team_stats(request, round_number, outround=False):
    """
    Returns the tab card data for all teams in the pairings of this given round number
    """
    if outround:
        pairings = tab_logic.sorted_pairings(round_number, outround=True)
    else:
        pairings = tab_logic.sorted_pairings(round_number)
    stats_by_team_id = {}

    def stats_for_team(team):
        stats = {}
        stats["seed"] = Team.get_seed_display(team).split(" ")[0]
        stats["wins"] = tab_logic.tot_wins(team)
        stats["total_speaks"] = tab_logic.tot_speaks(team)
        stats["govs"] = tab_logic.num_govs(team)
        stats["opps"] = tab_logic.num_opps(team)

        if hasattr(team, "breaking_team"):
            stats["outround_seed"] = team.breaking_team.seed
            stats["effective_outround_seed"] = team.breaking_team.effective_seed

        return stats

    for round_obj in pairings:
        if round_obj.gov_team:
            stats_by_team_id[round_obj.gov_team_id] = stats_for_team(
                round_obj.gov_team)
        if round_obj.opp_team:
            stats_by_team_id[round_obj.opp_team_id] = stats_for_team(
                round_obj.opp_team)

    return JsonResponse(stats_by_team_id)


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def assign_team(request, round_id, position, team_id):
    try:
        round_obj = Round.objects.get(id=int(round_id))
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
        round_obj = Round.objects.select_related("gov_team",
                                                 "opp_team").get(id=int(round_id))
        if not round_obj.gov_team or not round_obj.opp_team:
            return JsonResponse({"success": False})
        round_obj.gov_team, round_obj.opp_team = round_obj.opp_team, round_obj.gov_team
        if round_obj.pullup == Round.GOV:
            round_obj.pullup = Round.OPP
        elif round_obj.pullup == Round.OPP:
            round_obj.pullup = Round.GOV
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
        round_obj = Round.objects.get(id=int(round_id))
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


def toggle_pairing_released(request):
    old = TabSettings.get("pairing_released", 0)
    new_value = int(not old)
    TabSettings.set("pairing_released", new_value)

    invalidate_inround_public_pairings_cache()

    data = {"success": True, "pairing_released": new_value == 1}
    return JsonResponse(data)


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def toggle_current_round_ballots(request):
    current_round_number = TabSettings.get("cur_round") - 1
    action = request.GET.get("action", "advance")

    outstanding_ballots = Round.objects.filter(
        round_number=current_round_number,
        victor=Round.NONE,
    ).exists()

    latest_ballots_released = int(
        TabSettings.get("latest_ballots_released", 0) or 0
    )
    auto_release_round = max(TabSettings.get("cur_round", 1) - 2, 0)

    if action == "advance":
        if outstanding_ballots:
            return JsonResponse({
                "success": False,
                "error": "please wait until all ballots are in",
                "all_ballots_in": False,
            })
        latest_ballots_released = max(latest_ballots_released, current_round_number)
    elif action == "revert":
        latest_ballots_released = auto_release_round
    else:
        return JsonResponse({"success": False, "error": "Invalid action specified."})

    TabSettings.set("latest_ballots_released", latest_ballots_released)
    invalidate_public_ballot_cache()

    data = {
        "success": True,
        "round_number": current_round_number,
        "latest_ballots_released": latest_ballots_released,
        "current_round_ballots_released": (
            current_round_number > 0 and latest_ballots_released >= current_round_number
        ),
        "all_ballots_in": not outstanding_ballots,
    }
    return JsonResponse(data)


def export_pairings_csv_view(request):
    return export_pairings_csv(is_outround=False)


def view_rounds(request):
    number_of_rounds = TabSettings.objects.get(key="tot_rounds").value
    rounds = [(i, f"Round {i}", 0, "")
              for i in range(1, number_of_rounds + 1)]
    return render(request, "common/list_data.html", {
        "item_type": "round",
        "item_list": rounds,
        "show_delete": True
    })




def enter_result(request,
                 round_id,
                 form_class=ResultEntryForm,
                 ballot_code=None,
                 redirect_to="/pairings/status"):
    round_obj = Round.objects.prefetch_related(
        "judges",
        "chair",
        "gov_team",
        "gov_team__debaters",
        "opp_team",
        "opp_team__debaters",
        "roundstats_set",
        "roundstats_set__debater",
    ).get(id=round_id)

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
        if ballot_code:
            form_kwargs["ballot_code"] = ballot_code
        form = form_class(**form_kwargs)

    return render(
        request, "ballots/round_entry.html", {
            "form": form,
            "title": f"Entering Ballot for {round_obj}",
            "gov_team": round_obj.gov_team,
            "opp_team": round_obj.opp_team,
            "ballot_code": ballot_code,
            "action": request.path,
            "warn_judges_about_speaks": TabSettings.get(
                "warn_judges_about_speaks", True),
            "low_speak_warning_threshold": TabSettings.get(
                "low_speak_warning_threshold", 25),
            "high_speak_warning_threshold": TabSettings.get(
                "high_speak_warning_threshold", 34),
        })


def enter_multiple_results(request, round_id, num_entered):
    round_obj = Round.objects.get(id=round_id)
    num_entered = max(int(num_entered), 1)
    if request.method == "POST":
        forms = [
            ResultEntryForm(request.POST,
                            prefix=str(i),
                            round_instance=round_obj,
                            no_fill=True) for i in range(1, num_entered + 1)
        ]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            # result is of the format:
            # winner_1 => [(debater, role, speaks, rank), ...]
            # winner_2 => [(debater, role, sp ...]
            result = {}
            debaters = ResultEntryForm.GOV + ResultEntryForm.OPP
            for form in forms:
                cleaned_data = form.cleaned_data
                winner = cleaned_data["winner"]
                if winner not in result:
                    result[winner] = []

                result[winner].append([])
                for debater in debaters:
                    old_stats = RoundStats.objects.filter(round=round_obj,
                                                          debater_role=debater)
                    if old_stats:
                        old_stats.delete()
                    debater_obj = Debater.objects.get(
                        pk=cleaned_data[f"{debater}_debater"])
                    debater_role_obj = debater
                    speaks_obj = float(cleaned_data[f"{debater}_speaks"])
                    ranks_obj = int(cleaned_data[f"{debater}_ranks"])
                    result[winner][-1].append(
                        (debater_obj, debater_role_obj, speaks_obj, ranks_obj))
            # Validate the extracted data and return it
            all_good, error_msg = validate_panel(result)
            if all_good:
                final_scores, final_winner = score_panel(
                    result, "discard_minority" in request.POST)
                print(final_scores)
                for (debater, role, speaks, ranks) in final_scores:
                    RoundStats.objects.create(debater=debater,
                                              round=round_obj,
                                              speaks=speaks,
                                              ranks=ranks,
                                              debater_role=role)
                round_obj.victor = final_winner
                round_obj.save()
                return redirect_and_flash_success(
                    request, "Round entered successfully")
            else:
                forms[0].add_error("winner", forms[0].error_class([error_msg]))
    else:
        forms = [
            ResultEntryForm(prefix=str(i),
                            round_instance=round_obj,
                            no_fill=True) for i in range(1, num_entered + 1)
        ]
    return render(
        request, "ballots/round_entry_multiple.html", {
            "forms": forms,
            "title": f"Entering Ballots for {round_obj}",
            "gov_team": round_obj.gov_team,
            "opp_team": round_obj.opp_team,
            "warn_judges_about_speaks": TabSettings.get(
                "warn_judges_about_speaks", True),
            "low_speak_warning_threshold": TabSettings.get(
                "low_speak_warning_threshold", 25),
            "high_speak_warning_threshold": TabSettings.get(
                "high_speak_warning_threshold", 34),
        })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def confirm_start_new_tourny(request):
    return render(
        request, "common/confirm.html", {
            "link": "/pairing/start_tourny/",
            "confirm_text": "Create New Tournament",
            "title": "Are you sure?"
        })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def start_new_tourny(request):
    try:
        clear_db()
        TabSettings.set("cur_round", 1)
        TabSettings.set("tot_rounds", 5)
        TabSettings.set("lenient_late", 0)
        TabSettings.set("tournament_name", "New Tournament")
    except Exception:
        emit_current_exception()
        return redirect_and_flash_error(
            request, "Invalid tournament state, try resetting from a back-up")
    return redirect_and_flash_success(request, "New tournament started")


def clear_db():
    obj_types = [
        CheckIn, RoundStats, Round, Judge, Room, Scratch, TabSettings, Team,
        School, Debater
    ]
    list(map(delete_obj, obj_types))


def delete_obj(obj_type):
    objs = obj_type.objects.all()
    for obj in objs:
        obj_type.delete(obj)

def remove_judge(request, round_id, judge_id, is_outround=False):
    round_id, judge_id = int(round_id), int(judge_id)
    round_model = Outround if is_outround else Round
    round_obj = get_object_or_404(round_model, id=round_id)
    judge = get_object_or_404(Judge, id=judge_id)
    all_judges = list(round_obj.judges.all().order_by("-rank"))
    if judge in all_judges:
        round_obj.judges.remove(judge)
        all_judges.remove(judge)
        if round_obj.chair == judge:
            if all_judges:
                round_obj.chair = all_judges[0]
            else:
                round_obj.chair = None
            round_obj.save()
        return JsonResponse({"success": True})
    return redirect_and_flash_error(request, "Judge not found in round")

def assign_chair(request, round_id, chair_id, is_outround=False):
    round_id, chair_id = int(round_id), int(chair_id)
    round_model = Outround if is_outround else Round
    round_obj = get_object_or_404(round_model, id=round_id)
    chair = get_object_or_404(Judge, id=chair_id)
    if chair in round_obj.judges.all():
        try:
            round_obj.chair = chair
            round_obj.save()
            return JsonResponse({"success": True})
        except ValueError:
            return redirect_and_flash_error(request, "Chair could not be assigned")
    return redirect_and_flash_error(request, "Judge not found in round")


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def round_stats(request):
    from mittab.libs.tab_logic.stats import get_all_round_stats
    stats = get_all_round_stats()
    return render(request, "tab/round_stats.html", {
        "title": "Round Statistics",
        **stats
    })
