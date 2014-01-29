from django.shortcuts import render_to_response
from django.template import RequestContext
from django.http import Http404,HttpResponse,HttpResponseRedirect
from django.contrib.auth.decorators import permission_required
from django.utils import simplejson
from forms import TeamForm, TeamEntryForm, ScratchForm
from errors import *
from models import *
import mittab.libs.tab_logic
from mittab.libs.tab_logic import TabFlags, tot_speaks_deb, tot_ranks_deb, tot_speaks, tot_ranks
from datetime import datetime

def view_teams(request):
    def flags(team):
        result = 0
        if t.checked_in:
            result |= TabFlags.TEAM_CHECKED_IN
        else:
            result |= TabFlags.TEAM_NOT_CHECKED_IN
        return result
    
    c_teams = [(t.pk, t.name, flags(t), TabFlags.flags_to_symbols(flags(t)))
               for t in Team.objects.all().order_by("name")]
    all_flags = [[TabFlags.TEAM_CHECKED_IN, TabFlags.TEAM_NOT_CHECKED_IN]]
    filters, symbol_text = TabFlags.get_filters_and_symbols(all_flags)
    return render_to_response('list_data.html', 
                             {'item_type':'team',
                              'title': "Viewing All Teams",
                              'item_list': c_teams,
                              'filters': filters,
                              'symbol_text': symbol_text}, 
                              context_instance=RequestContext(request))

def view_team(request, team_id):
    team_id = int(team_id)
    try:
        team = Team.objects.get(pk=team_id)
        stats = []
        stats.append(("Wins", tab_logic.tot_wins(team)))
        stats.append(("Total Speaks", tab_logic.tot_speaks(team)))
        stats.append(("Govs", tab_logic.num_govs(team)))
        stats.append(("Opps", tab_logic.num_opps(team)))
        stats.append(("Opp Wins", tab_logic.opp_strength(team)))
        stats.append(("Been Pullup", tab_logic.pull_up_count(team)))
        stats.append(("Hit Pullup", tab_logic.hit_pull_up_count(team)))
    except Team.DoesNotExist:
        return render_to_response('error.html', 
                                 {'error_type': "View Team",
                                  'error_name': str(team_id),
                                  'error_info':"No such Team"}, 
                                  context_instance=RequestContext(request))
    if request.method == 'POST':
        form = TeamForm(request.POST,instance=team)
        if form.is_valid():
            try:
               form.save()
            except ValueError:
                return render_to_response('error.html', 
                                         {'error_type': "Team",
                                          'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"Team name cannot be validated, most likely a non-existent team"}, 
                                          context_instance=RequestContext(request))
            return render_to_response('thanks.html', 
                                     {'data_type': "Team",
                                      'data_name': "["+form.cleaned_data['name']+"]"}, 
                                      context_instance=RequestContext(request))
    else:
        form = TeamForm(instance=team)
        links = [('/team/'+str(team_id)+'/scratches/view/','Scratches for '+str(team.name), False),
                 ('/team/'+str(team_id)+'/delete/', 'Delete', True)]
        for deb in team.debaters.all():
            links.append(('/debater/'+str(deb.id)+'/', "View %s" % deb.name, False))
        return render_to_response('data_entry.html', 
                                 {'title':"Viewing Team: %s"%(team.name),
                                  'form': form,
                                  'links': links,
                                  'team_obj':team,
                                  'team_stats':stats}, 
                                  context_instance=RequestContext(request))
    
    return render_to_response('data_entry.html', 
                             {'form': form}, 
                             context_instance=RequestContext(request))
    
def enter_team(request):
    if request.method == 'POST':
        form = TeamEntryForm(request.POST)
        if form.is_valid():
            try:
                team = form.save()
            except ValueError:
                return render_to_response('error.html', 
                                         {'error_type': "Team",'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"Team name cannot be validated, most likely a duplicate school"}, 
                                          context_instance=RequestContext(request))
            num_forms = form.cleaned_data['number_scratches']
            if num_forms > 0:
                return HttpResponseRedirect('/team/'+str(team.pk)+'/scratches/add/'+str(num_forms))
            else:
                return render_to_response('thanks.html', 
                                         {'data_type': "Team",
                                          'data_name': "["+str(team.name)+"]",
                                          'data_modification': 'CREATED',
                                          'enter_again': True},
                                          context_instance=RequestContext(request))

    else:
        form = TeamEntryForm()
    return render_to_response('data_entry.html',
                             {'form': form, 'title': "Create Team"},
                              context_instance=RequestContext(request))
    
@permission_required('tab.team.can_delete', login_url="/403/")                               
def delete_team(request, team_id):
    team_id = int(team_id)
    try :
        team = Team.objects.get(pk=team_id)
        team.delete()
    except Team.DoesNotExist:
        return render_to_response('error.html', 
                                 {'error_type': "Team",
                                 'error_name': str(team_id),
                                 'error_info':"Team does not exist"}, 
                                 context_instance=RequestContext(request))
    return render_to_response('thanks.html', 
                             {'data_type': "Team",
                              'data_name': "["+str(team_id)+"]",
                              'data_modification': 'DELETED'}, 
                              context_instance=RequestContext(request))

def add_scratches(request, team_id, number_scratches):
    try:
        team_id,number_scratches = int(team_id),int(number_scratches)
    except ValueError:
        return render_to_response('error.html', 
                                 {'error_type': "Scratch",'error_name': "Data Entry",
                                  'error_info':"I require INTEGERS!"}, 
                                  context_instance=RequestContext(request))
    try:
        team = Team.objects.get(pk=team_id)
    except Team.DoesNotExist:
        return render_to_response('error.html', 
                                 {'error_type': "Add Scratches for Team",
                                  'error_name': str(team_id),
                                  'error_info':"No such Team"}, 
                                  context_instance=RequestContext(request))
        
    if request.method == 'POST':
        forms = [ScratchForm(request.POST, prefix=str(i)) for i in range(1,number_scratches+1)]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            for form in forms:
                form.save()
            return render_to_response('thanks.html', 
                                     {'data_type': "Scratches for team",
                                      'data_name': "["+str(team_id)+"]",
                                      'data_modification': "CREATED"},
                                      context_instance=RequestContext(request))            
    else:
        forms = [ScratchForm(prefix=str(i), initial={'team':team_id,'scratch_type':0}) for i in range(1,number_scratches+1)]
    return render_to_response('data_entry_multiple.html', 
                             {'forms': zip(forms,[None]*len(forms)),
                              'data_type':'Scratch',
                              'title':"Adding Scratch(es) for %s"%(team.name)}, 
                              context_instance=RequestContext(request))
    
def view_scratches(request, team_id):
    try:
        team_id = int(team_id)
    except ValueError:
        return render_to_response('error.html', 
                                 {'error_type': "Scratch",'error_name': "Delete",
                                  'error_info':"I require INTEGERS!"}, 
                                  context_instance=RequestContext(request))
    scratches = Scratch.objects.filter(team=team_id)
    number_scratches = len(scratches)
    team = Team.objects.get(pk=team_id)
    if request.method == 'POST':
        forms = [ScratchForm(request.POST, prefix=str(i),instance=scratches[i-1]) for i in range(1,number_scratches+1)]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            for form in forms:
                form.save()
            return render_to_response('thanks.html', 
                                     {'data_type': "Scratches for team",
                                      'data_name': "["+str(team_id)+"]",
                                      'data_modification': "EDITED"},
                                      context_instance=RequestContext(request))  
    else:
        forms = [ScratchForm(prefix=str(i), instance=scratches[i-1]) for i in range(1,len(scratches)+1)]
    delete_links = ["/team/"+str(team_id)+"/scratches/delete/"+str(scratches[i].id) for i in range(len(scratches))]
    links = [('/team/'+str(team_id)+'/scratches/add/1/','Add Scratch', False)]
    return render_to_response('data_entry_multiple.html', 
                             {'forms': zip(forms,delete_links),
                              'data_type':'Scratch',
                              'links':links,
                              'title':"Viewing Scratch Information for %s"%(team.name)}, 
                              context_instance=RequestContext(request))

@permission_required('tab.tab_settings.can_change', login_url="/403/")
def all_tab_cards(request):
    all_teams = Team.objects.all()
    return render_to_response('all_tab_cards.html',
                              locals(),
                              context_instance=RequestContext(request))

def tab_card(request, team_id):
    try:
        team_id = int(team_id)
    except ValueError:
        return render_to_response('error.html', 
                                 {'error_type': "Scratch",'error_name': "Delete",
                                  'error_info':"I require INTEGERS!"}, 
                                  context_instance=RequestContext(request))
    team = Team.objects.get(pk=team_id)
    rounds = [r for r in Round.objects.filter(gov_team=team)] + [r for r in Round.objects.filter(opp_team=team)]
    rounds.sort(key =lambda x: x.round_number)
    roundstats = [RoundStats.objects.filter(round=r) for r in rounds]
    debaters = [d for d in team.debaters.all()]
    d1,d2 = debaters[0], debaters[1]
    round_stats = []
    num_rounds = TabSettings.objects.get(key="tot_rounds").value
    cur_round = TabSettings.objects.get(key="cur_round").value
    blank = " ";
    for i in range(num_rounds):
        round_stats.append([blank]*7)

    for r in rounds:
        dstat1 = [k for k in RoundStats.objects.filter(debater=d1).filter(round=r).all()]
        dstat2 = [k for k in RoundStats.objects.filter(debater=d2).filter(round=r).all()]
        if not dstat2 and not dstat1:
            break
        if not dstat2:
            dstat1,dstat2 = dstat1[0], dstat1[1]
        elif not dstat1:
            dstat1,dstat2 = dstat2[0], dstat2[1]
        else: 
            dstat1,dstat2 = dstat1[0], dstat2[0]
        index = r.round_number-1
        round_stats[index][3] = " - ".join([j.name for j in r.judges.all()])
        round_stats[index][4] = (float(dstat1.speaks), float(dstat1.ranks))
        round_stats[index][5] = (float(dstat2.speaks), float(dstat2.ranks))
        round_stats[index][6] = (float(dstat1.speaks + dstat2.speaks), float(dstat1.ranks + dstat2.ranks))

        if r.gov_team == team:
            round_stats[index][2] = r.opp_team
            round_stats[index][0] = "G"
            if r.victor == 1:
                round_stats[index][1] = "W"
            elif r.victor == 2 :
                round_stats[index][1] = "L"
            elif r.victor == 3:
                round_stats[index][1] = "WF"
            elif r.victor == 4 :
                round_stats[index][1] = "LF"
            elif r.victor == 5 :
                round_stats[index][1] = "AD"
            elif r.victor == 6:
                round_stats[index][1] = "AW"
        elif r.opp_team == team:
            round_stats[index][2] = r.gov_team
            round_stats[index][0] = "O"
            if r.victor == 1:
                round_stats[index][1] = "L"
            elif r.victor == 2 :
                round_stats[index][1] = "W"
            elif r.victor == 3:
                round_stats[index][1] = "LF"
            elif r.victor == 4 :
                round_stats[index][1] = "WF"
            elif r.victor == 5 :
                round_stats[index][1] = "AD"
            elif r.victor == 6:
                round_stats[index][1] = "AW"

    for i in range(cur_round-1):
        if round_stats[i][6] == blank:
            round_stats[i][6] = (0,0)
    for i in range(1,cur_round-1):
        round_stats[i][6] = (round_stats[i][6][0] + round_stats[i-1][6][0],
                             round_stats[i][6][1] + round_stats[i-1][6][1])

    totals = [[0,0],[0,0],[0,0]]
    for r in rounds:
        index = r.round_number-1
        if round_stats[index][4]==blank or round_stats[index][5]==blank:
            continue
        totals[0][0] += round_stats[index][4][0]
        totals[0][1] += round_stats[index][4][1]
        totals[1][0] += round_stats[index][5][0]
        totals[1][1] += round_stats[index][5][1]
        totals[2][0] += round_stats[index][4][0] + round_stats[index][5][0]
        totals[2][1] += round_stats[index][4][1] + round_stats[index][5][1]
    
    #Error out if we don't have a bye
    try:
        bye_round = Bye.objects.get(bye_team = team).round_number
    except:
        bye_round = None
        
    return render_to_response('tab_card.html', 
                             {'team_name': team.name,
                              'team_school': team.school,
                              'debater_1': d1.name,
                              'debater_1_status': Debater.NOVICE_CHOICES[d1.novice_status][1],
                              'debater_2': d2.name,
                              'debater_2_status': Debater.NOVICE_CHOICES[d2.novice_status][1],
                              'round_stats': round_stats,
                              'd1st': tot_speaks_deb(d1),
                              'd1rt': tot_ranks_deb(d1),
                              'd2st': tot_speaks_deb(d2),
                              'd2rt': tot_ranks_deb(d2),
                              'ts': tot_speaks(team),
                              'tr': tot_ranks(team),
                              'bye_round': bye_round},
                              context_instance=RequestContext(request))

def rank_teams_ajax(request):
    return render_to_response('rank_teams.html',
                             {'title': "Team Rankings"},
                              context_instance=RequestContext(request))

def rank_teams(request):
    print "starting rankings: ", datetime.now()
    ranked_teams = tab_logic.rank_teams()
    print "Got ranked teams"
    teams = [(team,
              tab_logic.tot_wins(team),
              tab_logic.tot_speaks(team),
              tab_logic.tot_ranks(team))
              for team in ranked_teams]

    print "started novice rankings: ", datetime.now()
    ranked_novice_teams = tab_logic.rank_nov_teams()
    nov_teams = [(team,
                  tab_logic.tot_wins(team),
                  tab_logic.tot_speaks(team),
                  tab_logic.tot_ranks(team))
                  for team in ranked_novice_teams]

    print "Got ranked novice teams"
    return render_to_response('rank_teams_component.html',
                             {'varsity': teams,
                              'novice': nov_teams,
                              'title': "Team Rankings"},
                              context_instance=RequestContext(request))

def team_stats(request, team_id):
    team_id = int(team_id)
    try:
        team = Team.objects.get(pk=team_id)
        stats = {}
        stats["seed"] = Team.get_seed_display(team).split(" ")[0]
        stats["wins"] = tab_logic.tot_wins(team)
        stats["total_speaks"] = tab_logic.tot_speaks(team)
        stats["govs"] = tab_logic.num_govs(team)
        stats["opps"] = tab_logic.num_opps(team)
        data = {'success': True, 'result':stats}
    except Team.DoesNotExist:
        data = {'success': False}
    data = simplejson.dumps(data)
    return HttpResponse(data, mimetype='application/json')


