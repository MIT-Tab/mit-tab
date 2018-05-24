import random
import sys
import traceback
import time
import datetime
import os
import pprint

from django.shortcuts import render_to_response, render
from django.template import RequestContext
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.shortcuts import redirect

from errors import *
from models import *
from mittab.libs.errors import *
from mittab.libs import cache_logic, tab_logic, assign_judges, assign_rooms, backup
from django.shortcuts import redirect
from forms import ResultEntryForm, UploadBackupForm, score_panel, \
        validate_panel, EBallotForm


@permission_required('tab.tab_settings.can_change', login_url="/403/")
def swap_judges_in_round(request, src_round, src_judge, dest_round, dest_judge):
    try :
        src_judge = Judge.objects.get(id=int(src_judge))
        dest_judge = Judge.objects.get(id=int(dest_judge))

        src_round = Round.objects.get(id=int(src_round))
        dest_round = Round.objects.get(id=int(dest_round))

        dest_round.judge = src_judge
        src_round.judge = dest_judge
        dest_round.save()
        src_round.save()
        data = {"success":True}
    except Exception as e:
        emit_current_exception()
        data = {"success":False}
    return JsonResponse(data)

@permission_required('tab.tab_settings.can_change', login_url="/403/")
def swap_teams_in_round(request, src_round, src_team, dest_round, dest_team):
    try :
        src_team = Team.objects.get(id=int(src_team))
        dest_team = Team.objects.get(id=int(dest_team))
        if int(src_round) == int(dest_round):
            same_round = Round.objects.get(id=int(src_round))
            gov_team = same_round.gov_team
            opp_team = same_round.opp_team
            same_round.gov_team = opp_team
            same_round.opp_team = gov_team
            same_round.save()
        else:
            src_round = Round.objects.get(id=int(src_round))
            dest_round = Round.objects.get(id=int(dest_round))
            if src_round.gov_team == src_team:
                if dest_round.gov_team == dest_team:
                    # Swap the two gov teams
                    src_round.gov_team = dest_team
                    dest_round.gov_team = src_team
                else:
                    # Swap src_rounds gov team with 
                    # dest_round's opp team
                    src_round.gov_team = dest_team
                    dest_round.opp_team = src_team
            else:
                if dest_round.gov_team == dest_team:
                    # Swap src_rounds opp team with
                    # dest_round's gov team
                    src_round.opp_team = dest_team
                    dest_round.gov_team = src_team
                else:
                    # Swap the two opp teams
                    src_round.opp_team = dest_team
                    dest_round.opp_team = src_team
            dest_round.save()
            src_round.save()
        data = {'success':True}
    except Exception as e:
        emit_current_exception()
        data = {'success':False}
    return JsonResponse(data)


@permission_required('tab.tab_settings.can_change', login_url="/403/")
def pair_round(request):
    cache_logic.clear_cache()
    current_round = TabSettings.objects.get(key="cur_round")
    current_round_number = current_round.value
    if request.method == 'POST':
        # We should pair the round
        try:
            TabSettings.set('pairing_released', 0)
            backup.backup_round("round_%i_before_pairing" % (current_round_number))

            with transaction.atomic():
                tab_logic.pair_round()
                current_round.value = current_round.value + 1
                current_round.save()
        except Exception as exp:
            emit_current_exception()
            return render_to_response('error.html',
                                      {'error_type': "Pair Next Round",
                                       'error_name': "Pairing Round %s" % (current_round.value + 1),
                                       'error_info': "Could not pair next round because of: [{0}]".format(exp)},
                                        context_instance=RequestContext(request))
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

        msg = "All Rounds properly entered for Round %s" % (current_round_number - 1)
        ready_to_pair = "Yes"
        ready_to_pair_alt = "Checks passed!"
        try:
            tab_logic.have_properly_entered_data(current_round_number)
            check_status.append((msg, "Yes", "All rounds look good"))
        except PrevRoundNotEnteredError as e:
            ready_to_pair = "No"
            ready_to_pair_alt = str(e)
            check_status.append((msg, "No", "Not all rounds are entered. %s" % str(e)))
        except ByeAssignmentError as e:
            ready_to_pair = "No"
            ready_to_pair_alt = str(e)
            check_status.append((msg, "No", "You have a bye and results. %s" % str(e)))
        except NoShowAssignmentError as e:
            ready_to_pair = "No"
            ready_to_pair_alt = str(e) 
            check_status.append((msg, "No", "You have a noshow and results. %s" % str(e)))

        return render_to_response('pair_round.html',
                                  locals(),
                                  context_instance=RequestContext(request))

@permission_required('tab.tab_settings.can_change', login_url="/403/")
def assign_judges_to_pairing(request):
    current_round_number = TabSettings.get("cur_round") - 1
    if request.method == 'POST':
        panel_points, errors = [], []
        potential_panel_points = [k for k in request.POST.keys() if k.startswith('panel_')]
        for point in potential_panel_points:
           try:
               point = int(point.split("_")[1])
               num = float(request.POST["panel_{0}".format(point)])
               if num > 0.0:
                   panel_points.append((Round.objects.get(id=point), num))
           except Exception as e:
               errors.append(e)
               pass

        panel_points.reverse()
        rounds = list(Round.objects.filter(round_number=current_round_number))
        judges = [ci.judge for ci in CheckIn.objects.filter(round_number=current_round_number)]
        try:
            backup.backup_round("round_%s_before_judge_assignment" % current_round_number)
            assign_judges.add_judges(rounds, judges, panel_points)
            assign_rooms.add_rooms(rounds)
        except Exception as e:
            emit_current_exception()
            return render_to_response('error.html',
                                     {'error_type': "Judge Assignment",
                                      'error_name': "",
                                      'error_info': str(e)},
                                      context_instance=RequestContext(request))
    return redirect('/pairings/status/')


@permission_required('tab.tab_settings.can_change', login_url='/403/')
def view_backup(request, filename):
    backups = backup.list_backups()
    item_list = []
    item_type='backup'
    title = "Viewing Backup: {}".format(filename)
    item_manip = "restore from that backup"
    links = [('/backup/download/{}/'.format(filename), "Download Backup", False),
             ('/backup/restore/{}/'.format(filename), "Restore From Backup", True)]
    return render_to_response('list_data.html', locals(), context_instance=RequestContext(request))

@permission_required('tab.tab_settings.can_change', login_url='/403/')
def download_backup(request, filename):
    print "Trying to download {}".format(filename)
    wrapper, size = backup.get_wrapped_file(filename)
    response = HttpResponse(wrapper, content_type='text/plain')
    response['Content-Length'] = size
    response['Content-Disposition'] = "attachment; filename=%s" % filename
    return response

@permission_required('tab.tab_settings.can_change', login_url='/403/')
def upload_backup(request):
    if request.method == 'POST':
        form = UploadBackupForm(request.POST, request.FILES)
        if form.is_valid():
            backup.handle_backup(request.FILES['file'])
            return render_to_response('thanks.html', 
                                     {'data_type': "Backup",
                                      'data_name': request.FILES['file'].name,
                                      'data_modification': "CREATE"}, 
                                      context_instance=RequestContext(request))
    else:
        form = UploadBackupForm()
    return render_to_response('data_entry.html', 
                              {'form': form,
                               'title': 'Upload a Backup'}, 
                               context_instance=RequestContext(request))

@permission_required('tab.tab_settings.can_change', login_url="/403/")
def manual_backup(request):
    try:
        cur_round, btime = TabSettings.objects.get(key="cur_round").value, int(time.time())
        now = datetime.datetime.fromtimestamp(btime).strftime("%Y-%m-%d_%I:%M")
        backup.backup_round("manual_backup_round_{}_{}_{}".format(cur_round, btime, now))
    except:
        emit_current_exception()
        return render_to_response('error.html',
                                 {'error_type': "Manual Backup",'error_name': "Backups",
                                  'error_info': "Could not backup database. Something is wrong with your AWS setup."},
                                  context_instance=RequestContext(request))
    return render_to_response('thanks.html',
                             {'data_type': "Backing up database",
                              'data_name': " for round {} as version number {}".format(cur_round, btime)},
                               context_instance=RequestContext(request))

@permission_required('tab.tab_settings.can_change', login_url="/403/")
def view_backups(request):
    backups = backup.list_backups()
    item_list = [(i,i) for i in sorted(backups)]
    item_type='backup'
    title = "Viewing All Backups"
    item_manip = "restore from that backup"
    links = [('/upload_backup/', "Upload Backup", False)]
    return render_to_response('list_data.html', locals(), context_instance=RequestContext(request))

@permission_required('tab.tab_settings.can_change', login_url="/403/")
def restore_backup(request, filename):
    print "Trying to restore %s" % filename
    backup.restore_from_backup(filename)
    return render_to_response('thanks.html',
                             {'data_type': "Restored from backup",
                              'data_name': "{}".format(filename)},
                               context_instance=RequestContext(request))

def view_status(request):
    current_round_number = TabSettings.objects.get(key="cur_round").value - 1
    return view_round(request, current_round_number)

def view_round(request, round_number, errors = None):
    if errors is None:
        errors = []
    valid_pairing, byes = True, []
    round_pairing = list(Round.objects.filter(round_number=round_number))

    random.seed(1337)
    random.shuffle(round_pairing)
    round_pairing.sort(key = lambda x: tab_logic.team_comp(x, round_number),
                       reverse = True)

    #For the template since we can't pass in something nicer like a hash
    round_info = [pair for pair in round_pairing]

    paired_teams = [team.gov_team for team in round_pairing] + [team.opp_team for team in round_pairing]
    n_over_two = Team.objects.filter(checked_in=True).count() / 2
    valid_pairing = len(round_pairing) >= n_over_two or round_number == 0
    for present_team in Team.objects.filter(checked_in=True):
        if not (present_team in paired_teams):
            errors.append("%s was not in the pairing" % (present_team))
            byes.append(present_team)
    pairing_exists = len(round_pairing) > 0
    pairing_released = TabSettings.get("pairing_released", 0) == 1
    judges_assigned = all((r.judges.count() > 0 for r in round_info))
    excluded_judges = Judge.objects.exclude(judges__round_number=round_number).filter(checkin__round_number = round_number)
    non_checkins = Judge.objects.exclude(judges__round_number=round_number).exclude(checkin__round_number = round_number)
    available_rooms = Room.available_for_round(round_number)
    size = max(map(len, [excluded_judges, non_checkins, byes]))
    # The minimum rank you want to warn on
    warning = 5
    judge_slots = [1,2,3]

    # A seemingly complex one liner to do a fairly simple thing
    # basically this generates the table that the HTML will display such that the output looks like:
    # [ Byes ][Judges not in round but checked in][Judges not in round but not checked in]
    # [ Team1][             CJudge1              ][                 Judge1               ]
    # [ Team2][             CJudge2              ][                 Judge2               ]
    # [      ][             CJudge3              ][                 Judge3               ]
    # [      ][                                  ][                 Judge4               ]
    excluded_people = zip(*map( lambda x: x+[""]*(size-len(x)), [list(byes), list(excluded_judges), list(non_checkins), list(available_rooms)]))

    return render_to_response('pairing_control.html',
                               locals(),
                               context_instance=RequestContext(request))

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
        current_judge_id, current_judge_obj, current_judge_rank = "","",""
        current_judge_name = "No judge"
    excluded_judges = Judge.objects.exclude(judges__round_number = round_number) \
                                   .filter(checkin__round_number = round_number)
    included_judges = Judge.objects.filter(judges__round_number = round_number) \
                                   .filter(checkin__round_number = round_number)
    excluded_judges = [(j.name, j.id, float(j.rank))
                       for j in
                       assign_judges.can_judge_teams(excluded_judges, round_gov, round_opp)]
    included_judges = [(j.name, j.id, float(j.rank))
                       for j in
                       assign_judges.can_judge_teams(included_judges, round_gov, round_opp)]
    included_judges = sorted(included_judges, key=lambda x: -x[2])
    excluded_judges = sorted(excluded_judges, key=lambda x: -x[2])

    return render_to_response('judge_dropdown.html',
                              locals(),
                              context_instance=RequestContext(request))

@permission_required('tab.tab_settings.can_change', login_url="/403/")
def assign_judge(request, round_id, judge_id, remove_id=None):
    try :
        round_obj = Round.objects.get(id=int(round_id))
        judge_obj = Judge.objects.get(id=int(judge_id))
        round_obj.judges.add(judge_obj)

        if remove_id is not None:
            remove_obj = Judge.objects.get(id=int(remove_id))
            round_obj.judges.remove(remove_obj)

            if remove_obj == round_obj.chair:
                round_obj.chair = round_obj.judges.order_by('-rank').first()

        round_obj.save()
        data = {"success":True,
                "round_id": round_obj.id,
                "judge_name": judge_obj.name,
                "judge_rank": float(judge_obj.rank),
                "judge_id": judge_obj.id}
    except Exception as e:
        emit_current_exception()
        data = {"success": False}
    return JsonResponse(data)

def toggle_pairing_released(request):
    old = TabSettings.get("pairing_released", 0)
    TabSettings.set("pairing_released", int(not old))
    data = {"success": True,
            "pairing_released": int(not old) == 1}
    return JsonResponse(data)

"""dxiao: added a html page for showing tab for the current round.
Uses view_status and view_round code from revision 108."""
def pretty_pair(request, printable=False):

    errors, byes = [], []

    round_number = TabSettings.get("cur_round") - 1
    round_pairing = list(Round.objects.filter(round_number = round_number))

    #We want a random looking, but constant ordering of the rounds
    random.seed(0xBEEF)
    random.shuffle(round_pairing)
    round_pairing.sort(key=lambda r: r.gov_team.name)
    paired_teams = [team.gov_team for team in round_pairing] + [team.opp_team for team in round_pairing]

    byes = [bye.bye_team for bye in Bye.objects.filter(round_number=round_number)]
    team_count = len(paired_teams) + len(byes)

    print "getting errors"
    for present_team in Team.objects.filter(checked_in=True):
        if not (present_team in paired_teams):
            if present_team not in byes:
                print "got error for", present_team
                errors.append(present_team)

    pairing_exists = TabSettings.get("pairing_released", 0) == 1
    printable = printable
    return render_to_response('round_pairings.html',
                               locals(),
                               context_instance=RequestContext(request))

def pretty_pair_print(request):
    return pretty_pair(request, True)

def view_rounds(request):
    number_of_rounds = TabSettings.objects.get(key="tot_rounds").value
    rounds = [(i, "Round %i" % i) for i in range(1,number_of_rounds+1)]
    return render_to_response('list_data.html',
                              {'item_type':'round',
                               'item_list': rounds,
                               'show_delete': True},
                              context_instance=RequestContext(request))


def e_ballot_search(request):
    if request.method == "POST":
        return redirect("/e_ballots/%s" % request.POST.get("ballot_code"))
    else:
        return render(request, "e_ballot_search.html")


def enter_e_ballot(request, ballot_code):
    if request.method == "POST":
        round_id = request.POST.get("round_instance")

        if round_id:
            return enter_result(request, round_id, EBallotForm, ballot_code)
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
        return enter_result(request, rounds.first().id, EBallotForm, ballot_code)

    return render(request, "error.html",
                {"error_type": "Ballot Retrieval", "error_info": message})


def enter_result(request, round_id, form_class=ResultEntryForm, ballot_code=None):
    round_obj = Round.objects.get(id=round_id)

    if request.method == "POST":
        form = form_class(request.POST, round_instance=round_obj)
        if form.is_valid():
            try:
                result = form.save()
            except ValueError:
                return render_to_response("error.html",
                                          {"error_type": "Round Result",
                                           "error_name": "[%s]" % str(round_obj),
                                           "error_info": "Invalid round result, could not remedy."},
                                          context_instance=RequestContext(request))
            return render_to_response("thanks.html",
                                      {"data_type": "Round Result",
                                       "data_name": "[%s]" % str(round_obj)},
                                      context_instance=RequestContext(request))
    else:
        form_kwargs = { "round_instance": round_obj }
        if ballot_code:
            form_kwargs["ballot_code"] = ballot_code
        form = form_class(**form_kwargs)

    return render_to_response("round_entry.html",
                              {"form": form,
                               "title": u'Entering Ballot for {}'.format(round_obj),
                               "gov_team": round_obj.gov_team,
                               "opp_team": round_obj.opp_team,
                               "ballot_code": ballot_code},
                              context_instance=RequestContext(request))


def enter_multiple_results(request, round_id, num_entered):
    round_obj = Round.objects.get(id=round_id)
    num_entered = max(int(num_entered), 1)
    if request.method == 'POST':
        forms = [ResultEntryForm(request.POST,
                                 prefix=str(i),
                                 round_instance=round_obj,
                                 no_fill = True)
                 for i in range(1, num_entered +1)]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            # result is of the format:
            # winner_1 => [(debater, role, speaks, rank), (debater, role, speaks, rank) ...]
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
                    old_stats = RoundStats.objects.filter(round=round_obj, debater_role = debater)
                    if len(old_stats) > 0:
                        old_stats.delete()
                    debater_obj = Debater.objects.get(pk=cleaned_data["%s_debater"%(debater)])
                    debater_role_obj = debater
                    speaks_obj, ranks_obj = float(cleaned_data["%s_speaks"%(debater)]),int(cleaned_data["%s_ranks"%(debater)])
                    result[winner][-1].append((debater_obj, debater_role_obj, speaks_obj, ranks_obj))
            # Validate the extracted data and return it
            all_good, error_msg = validate_panel(result)
            if all_good:
                final_scores, final_winner = score_panel(result, "discard_minority" in request.POST)
                print final_scores
                for (debater, role, speaks, ranks) in final_scores:
                    RoundStats.objects.create(debater = debater,
                                              round = round_obj,
                                              speaks = speaks,
                                              ranks = ranks,
                                              debater_role = role)
                round_obj.victor = final_winner
                round_obj.save()
                return render_to_response('thanks.html', 
                                         {'data_type': "Round Result",
                                          'data_name': "["+str(round_obj)+"]"}, 
                                          context_instance=RequestContext(request))
            else:
                forms[0]._errors["winner"] = forms[0].error_class([error_msg])
    else:
        forms = [ResultEntryForm(prefix = str(i),
                                 round_instance=round_obj,
                                 no_fill = True) for i in range(1, num_entered + 1)]
    return render_to_response('round_entry_multiple.html',
                              {'forms': forms,
                               'title': "Entering Ballots for {}".format(str(round_obj)),
                               'gov_team': round_obj.gov_team,
                               'opp_team': round_obj.opp_team},
                               context_instance=RequestContext(request))

@permission_required('tab.tab_settings.can_change', login_url="/403/")
def confirm_start_new_tourny(request):
    return render_to_response('confirm.html',
                              {'link': "/pairing/start_tourny/",
                               'confirm_text': "Create New Tournament"},
                               context_instance=RequestContext(request))

#TODO: Unify this with initialize_tourney
@permission_required('tab.tab_settings.can_change', login_url="/403/")
def start_new_tourny(request):
    try:
        clear_db()
        #TODO: Unify this with initialize_tourney
        TabSettings.set("cur_round", 1)
        TabSettings.set("tot_rounds", 5)
        TabSettings.set("lenient_late", 0)
    except Exception as e:
        emit_current_exception()
        return render_to_response('error.html',
                            {'error_type': "Could not Start Tournament",
                            'error_name': "",
                            'error_info':"Invalid Tournament State. Time to hand tab. [%s]"%(e)},
                            context_instance=RequestContext(request))
    return render_to_response('thanks.html',
                            {'data_type': "Started New Tournament",
                            'data_name': ""},
                            context_instance=RequestContext(request))

def clear_db():
    check_ins = CheckIn.objects.all()
    for i in range(len(check_ins)):
        CheckIn.delete(check_ins[i])
    print "Cleared Checkins"

    round_stats = RoundStats.objects.all()
    for i in range(len(round_stats)):
        RoundStats.delete(round_stats[i])
    print "Cleared RoundStats"

    rounds = Round.objects.all()
    for i in range(len(rounds)):
        Round.delete(rounds[i])
    print "Cleared Rounds"

    judges = Judge.objects.all()
    for i in range(len(judges)):
        Judge.delete(judges[i])
    print "Cleared Judges"

    rooms = Room.objects.all()
    for i in range(len(rooms)):
        Room.delete(rooms[i])
    print "Cleared Rooms"

    scratches = Scratch.objects.all()
    for i in range(len(scratches)):
        Scratch.delete(scratches[i])
    print "Cleared Scratches"

    tab_set = TabSettings.objects.all()
    for i in range(len(tab_set)):
        TabSettings.delete(tab_set[i])
    print "Cleared TabSettings"

    teams = Team.objects.all()
    for i in range(len(teams)):
        Team.delete(teams[i])
    print "Cleared Teams"

    debaters = Debater.objects.all()
    for i in range(len(debaters)):
        Debater.delete(debaters[i])
    print "Cleared Debaters"

    schools = School.objects.all()
    for i in range(len(schools)):
        School.delete(schools[i])
    print "Cleared Schools"

