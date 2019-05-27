import random
import time
import datetime

from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import logout
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.shortcuts import redirect

from mittab.apps.tab.helpers import redirect_and_flash_error, \
        redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs.errors import *
from mittab.apps.tab.forms import ResultEntryForm, UploadBackupForm, score_panel, \
        validate_panel, EBallotForm
import mittab.libs.cache_logic as cache_logic
import mittab.libs.tab_logic as tab_logic
import mittab.libs.assign_judges as assign_judges
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
            backup.backup_round("round_%i_before_pairing" %
                                (current_round_number))

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
        panel_points, errors = [], []
        potential_panel_points = [
            k for k in list(request.POST.keys()) if k.startswith("panel_")
        ]
        for point in potential_panel_points:
            try:
                point = int(point.split("_")[1])
                num = float(request.POST["panel_{0}".format(point)])
                if num > 0.0:
                    panel_points.append((Round.objects.get(id=point), num))
            except Exception as e:
                emit_current_exception()
                errors.append(e)

        panel_points.reverse()
        rounds = list(Round.objects.filter(round_number=current_round_number))
        judges = [
            ci.judge for ci in JudgeCheckIn.objects.filter(
                round_number=current_round_number)
        ]
        try:
            backup.backup_round("round_%s_before_judge_assignment" %
                                current_round_number)
            assign_judges.add_judges(rounds, judges, panel_points)
        except Exception as e:
            emit_current_exception()
            return redirect_and_flash_error(
                request, "Got error during judge assignment")
    return redirect("/pairings/status/")


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def view_backup(request, filename):
    backups = backup.list_backups()
    item_list = []
    item_type = "backup"
    title = "Viewing Backup: {}".format(filename)
    item_manip = "restore from that backup"
    links = [("/backup/download/{}/".format(filename), "Download Backup"),
             ("/backup/restore/{}/".format(filename), "Restore From Backup")]
    return render(request, "common/list_data.html", locals())


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def download_backup(request, filename):
    print("Trying to download {}".format(filename))
    wrapper, size = backup.get_wrapped_file(filename)
    response = HttpResponse(wrapper, content_type="text/plain")
    response["Content-Length"] = size
    response["Content-Disposition"] = "attachment; filename=%s" % filename
    return response


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def upload_backup(request):
    if request.method == "POST":
        form = UploadBackupForm(request.POST, request.FILES)
        if form.is_valid():
            backup.handle_backup(request.FILES["file"])
            return redirect_and_flash_success(
                request, "Backup {} uploaded successfully".format(
                    request.FILES["file"].name))
    else:
        form = UploadBackupForm()
    return render(request, "common/data_entry.html", {
        "form": form,
        "title": "Upload a Backup"
    })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def manual_backup(request):
    try:
        cur_round, btime = TabSettings.objects.get(key="cur_round").value, int(
            time.time())
        now = datetime.datetime.fromtimestamp(btime).strftime("%Y-%m-%d_%I:%M")
        backup.backup_round("manual_backup_round_{}_{}_{}".format(
            cur_round, btime, now))
    except Exception:
        emit_current_exception()
        return redirect_and_flash_error(request, "Error creating backup")
    return redirect_and_flash_success(
        request,
        "Backup created for round {} at timestamp {}".format(cur_round, btime))


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def view_backups(request):
    backups = backup.list_backups()
    item_list = [(i, i, 0, "") for i in sorted(backups)]
    item_type = "backup"
    title = "Viewing All Backups"
    item_manip = "restore from that backup"
    links = [("/upload_backup/", "Upload Backup")]
    return render(request, "common/list_data.html", locals())


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def restore_backup(request, filename):
    backup.restore_from_backup(filename)
    logout(request)
    return redirect_and_flash_success(
        request,
        "Restored from backup. You have been logged out as a result.",
        path="/")


def view_status(request):
    current_round_number = TabSettings.objects.get(key="cur_round").value - 1
    return view_round(request, current_round_number)


def view_round(request, round_number):
    errors, excluded_teams = [], []
    round_pairing = list(Round.objects.filter(round_number=round_number))

    random.seed(1337)
    random.shuffle(round_pairing)
    round_pairing.sort(key=lambda x: tab_logic.team_comp(x, round_number),
                       reverse=True)

    #For the template since we can't pass in something nicer like a hash
    round_info = [pair for pair in round_pairing]

    paired_teams = [team.gov_team for team in round_pairing
                    ] + [team.opp_team for team in round_pairing]
    n_over_two = Team.objects.filter(checked_in=True).count() / 2

    for present_team in Team.objects.filter(checked_in=True):
        if present_team not in paired_teams:
            excluded_teams.append(present_team)

    for team in excluded_teams:
        if not Bye.objects.filter(round_number=round_number,
                                  bye_team=team).exists():
            errors.append(
                "{} is checked-in but has no round or bye".format(team))

    pairing_exists = len(round_pairing) > 0
    pairing_released = TabSettings.get("pairing_released", 0) == 1
    judges_assigned = all((r.judges.count() > 0 for r in round_info))
    excluded_judges = Judge.checked_in(round_number).exclude(
        judges__round_number=round_number)
    non_checkins = Judge.checked_out(round_number).exclude(
        judges__round_number=round_number)
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
    # All of these variables are for the convenience of the template
    try:
        current_judge_id = int(judge_id)
        current_judge_obj = Judge.objects.get(id=current_judge_id)
        current_judge_name = current_judge_obj.name
        current_judge_rank = current_judge_obj.rank
    except TypeError:
        current_judge_id, current_judge_obj, current_judge_rank = "", "", ""
        current_judge_name = "No judge"
    excluded_judges = Judge.checked_in(round_number) \
                                    .exclude(judges__round_number=round_number)
    included_judges = Judge.checked_in(round_number) \
                                   .filter(judges__round_number=round_number)
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
    round_obj = Round.objects.get(pk=round_id)
    current_team = Team.objects.get(pk=current_team_id)
    round_number = round_obj.round_number
    excluded_teams = Team.objects.exclude(gov_team__round_number=round_number) \
        .exclude(opp_team__round_number=round_number) \
        .exclude(pk=current_team_id)
    included_teams = Team.objects.exclude(pk__in=excluded_teams) \
        .exclude(pk=current_team_id)
    return render(request, "pairing/team_dropdown.html", locals())


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


def pretty_pair(request, printable=False):
    errors, byes = [], []

    round_number = TabSettings.get("cur_round") - 1
    round_pairing = list(Round.objects.filter(round_number=round_number))

    #We want a random looking, but constant ordering of the rounds
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
                print("got error for", present_team)
                errors.append(present_team)

    pairing_exists = TabSettings.get("pairing_released", 0) == 1
    printable = printable
    return render(request, "pairing/pairing_display.html", locals())


def pretty_pair_print(request):
    return pretty_pair(request, True)


def missing_ballots(request):
    round_number = TabSettings.get("cur_round") - 1
    rounds = Round.objects.filter(victor=Round.NONE, round_number=round_number)
    # need to do this to not reveal brackets

    rounds = sorted(rounds, key=lambda r: r.chair.name if r.chair else "")
    pairing_exists = TabSettings.get("pairing_released", 0) == 1
    return render(request, "ballots/missing_ballots.html", locals())


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
        return redirect("/e_ballots/%s" % request.POST.get("ballot_code"))
    else:
        return redirect("/404")


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
    rounds = Round.objects.filter(judges__ballot_code=ballot_code.lower())
    rounds = rounds.filter(round_number=current_round)
    judge = Judge.objects.filter(ballot_code=ballot_code).first()

    if not judge:
        message = """
                    No judges with the ballot code "%s." Try submitting again, or
                    go to tab to resolve the issue.
                    """ % ballot_code
    elif TabSettings.get("pairing_released", 0) != 1:
        message = "Pairings for this round have not been released."
    elif rounds.count() > 1:
        message = """
                Found more than one ballot for you this round.
                Go to tab to resolve this error.
                """
    elif rounds.count() == 0:
        message = """
                Could not find a ballot for you this round. Go to tab
                to resolve the issue if you believe you were paired in.
                """
    elif rounds.first().chair != judge:
        message = """
                You are not the chair of this round. If you are on a panel,
                only the chair can submit an e-ballot. If you are not on a
                panel, go to tab and make sure the chair is properly set for
                the round.
                """
    else:
        return enter_result(request,
                            rounds.first().id, EBallotForm, ballot_code)
    return redirect_and_flash_error(request, message, path="/accounts/login")


def enter_result(request,
                 round_id,
                 form_class=ResultEntryForm,
                 ballot_code=None,
                 redirect_to="/pairings/status"):
    round_obj = Round.objects.get(id=round_id)

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
            "ballot_code": ballot_code
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
