import random
import datetime
import os

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import logout
from django.contrib.auth.decorators import permission_required
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
import mittab.libs.cache_logic as cache_logic
from mittab.libs.data_export.pairings_export import export_pairings_csv
import mittab.libs.tab_logic as tab_logic
import mittab.libs.assign_judges as assign_judges
from mittab.libs.assign_judges import judge_team_rejudge_counts
import mittab.libs.backup as backup


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
                current_round.value = current_round.value + 1
                current_round.save()
        except Exception as exp:
            emit_current_exception()
            return redirect_and_flash_error(
                request,
                "Could not pair next round, got error: {}".format(exp))
        return view_status(request)
    else:
        # See if we can pair the round
        title = "Pairing Round %s" % (current_round_number)
        check_status = []

        judges = tab_logic.have_enough_judges(current_round_number)
        rooms = tab_logic.have_enough_rooms(current_round_number)

        msg = "N/2 Judges checked in for Round {0}? Need {1}, have {2}".format(
            current_round_number, judges[1][1], judges[1][0])
        if judges[0]:
            check_status.append((msg, "Yes", "Judges are checked in"))
        else:
            check_status.append((msg, "No", "Not enough judges"))

        msg = "N/2 Rooms available Round {0}? Need {1}, have {2}".format(
            current_round_number, rooms[1][1], rooms[1][0])
        if rooms[0]:
            check_status.append((msg, "Yes", "Rooms are checked in"))
        else:
            check_status.append((msg, "No", "Not enough rooms"))

        msg = "All Rounds properly entered for Round %s" % (
            current_round_number - 1)
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
    title = "Viewing Backup: {}".format(name)
    links = [("/backup/download/{}/".format(filename), "Download Backup"),
             ("/backup/restore/{}/".format(filename), "Restore From Backup")]
    return render(request, "common/list_data.html", locals())


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def download_backup(request, key):
    print("Trying to download {}".format(key))
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
            return redirect_and_flash_success(
                request, "Backup {} uploaded successfully".format(
                    request.FILES["file"].name),
                path="/pairing/view_backups/")
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

    return render(request, "pairing/pairing_control.html", locals())


def alternative_judges(request, round_id, judge_id=None):
    round_obj = Round.objects.get(id=int(round_id))
    round_number = round_obj.round_number
    round_gov, round_opp = round_obj.gov_team, round_obj.opp_team
    excluded_judges = Judge.objects.exclude(judges__round_number=round_number) \
                                   .filter(checkin__round_number=round_number) \
                                   .prefetch_related("judges", "scratches")
    included_judges = Judge.objects.filter(judges__round_number=round_number) \
                                   .filter(checkin__round_number=round_number) \
                                   .prefetch_related("judges", "scratches")

    excluded_judges_list = assign_judges.can_judge_teams(
        excluded_judges, round_gov, round_opp)
    included_judges_list = assign_judges.can_judge_teams(
        included_judges, round_gov, round_opp)

    current_judge_obj = None
    try:
        current_judge_id = int(judge_id)
        current_judge_obj = Judge.objects.prefetch_related(
            "judges", "scratches").get(id=current_judge_id)
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

    return render(request, "pairing/judge_dropdown.html", locals())


def alternative_teams(request, round_id, current_team_id, position):
    round_obj = Round.objects.get(pk=round_id)
    current_team = Team.objects.get(pk=current_team_id)
    round_number = round_obj.round_number
    excluded_teams = Team.objects.exclude(gov_team__round_number=round_number) \
        .exclude(opp_team__round_number=round_number) \
        .exclude(pk=current_team_id)
    included_teams = Team.objects.exclude(pk__in=excluded_teams) \
        .exclude(pk=current_team_id)
    return render(request, "pairing/team_dropdown.html", locals())


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
    TabSettings.set("pairing_released", int(not old))
    data = {"success": True, "pairing_released": int(not old) == 1}
    return JsonResponse(data)


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
        bye.bye_team for bye in Bye.objects.filter(round_number=round_number)
    ]
    team_count = len(paired_teams) + len(byes)

    for present_team in Team.objects.filter(checked_in=True):
        if present_team not in paired_teams:
            if present_team not in byes:
                errors.append(present_team)

    pairing_exists = TabSettings.get("pairing_released", 0) == 1
    debater_team_memberships_public = TabSettings.get("debaters_public", 1)
    return render(request, "pairing/pairing_display.html", locals())


def export_pairings_csv_view(request):
    return export_pairings_csv(is_outround=False)


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
        "ballots/missing_ballots.html",
        {
            "rounds": rounds,
            "pairing_exists": pairing_exists,
        },
    )

def view_rounds(request):
    number_of_rounds = TabSettings.objects.get(key="tot_rounds").value
    rounds = [(i, "Round %i" % i, 0, "")
              for i in range(1, number_of_rounds + 1)]
    return render(request, "common/list_data.html", {
        "item_type": "round",
        "item_list": rounds,
        "show_delete": True
    })


def e_ballot_search(request):
    if request.method == "POST":
        ballot_code = (request.POST.get("ballot_code") or "").strip()
        if ballot_code:
            return redirect("enter_e_ballot", ballot_code=ballot_code)
        return redirect_and_flash_error(
            request,
            "Please enter the ballot code provided by tab.",
            path="/e_ballots/",
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
    # see above, judge.judges is rounds
    rounds = list(judge.judges.prefetch_related("chair")
                  .filter(round_number=current_round).all())

    if not judge:
        message = """
                    No judges with the ballot code "%s." Try submitting again, or
                    go to tab to resolve the issue.
                    """ % ballot_code
    elif TabSettings.get("pairing_released", 0) != 1:
        message = "Pairings for this round have not been released."
    elif len(rounds) > 1:
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
    return redirect_and_flash_error(request, message, path="/accounts/login")


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
            "title": "Entering Ballot for {}".format(round_obj),
            "gov_team": round_obj.gov_team,
            "opp_team": round_obj.opp_team,
            "ballot_code": ballot_code,
            "action": request.path,
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
                        pk=cleaned_data["%s_debater" % (debater)])
                    debater_role_obj = debater
                    speaks_obj, ranks_obj = float(
                        cleaned_data["%s_speaks" % (debater)]), int(
                            cleaned_data["%s_ranks" % (debater)])
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
            "title": "Entering Ballots for {}".format(str(round_obj)),
            "gov_team": round_obj.gov_team,
            "opp_team": round_obj.opp_team
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
    except Exception:
        emit_current_exception()
        return redirect_and_flash_error(
            request, "Invalid tournament state, try resetting from a back-up")
    return redirect_and_flash_success(request, "New tournament started")


def clear_db():
    obj_types = [
        CheckIn, RoundStats, Round, Judge, Room, Scratch, TabSettings, Team,
        School
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
