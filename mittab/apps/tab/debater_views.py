from django.shortcuts import render_to_response
from django.template import RequestContext
from django.http import Http404,HttpResponse,HttpResponseRedirect
from django.contrib.auth.decorators import permission_required
from mittab.apps.tab.forms import DebaterForm
from mittab.apps.tab.errors import *
from mittab.apps.tab.models import *

from mittab.libs import tab_logic, errors

def view_debaters(request):
    #Get a list of (id,debater_name) tuples
    c_debaters = [(debater.pk,debater.name) for debater in Debater.objects.order_by("name")]
    return render_to_response('list_data.html', 
                             {'item_type':'debater',
                              'title': "Viewing All Debaters",
                              'item_list':c_debaters}, context_instance=RequestContext(request))

def view_debater(request, debater_id):
    debater_id = int(debater_id)
    try:
        debater = Debater.objects.get(pk=debater_id)
    except Debater.DoesNotExist:
        return render_to_response('error.html', 
                                 {'error_type': "View Debater",
                                  'error_name': str(debater_id),
                                  'error_info':"No such debater"}, 
                                  context_instance=RequestContext(request))
    if request.method == 'POST':
        form = DebaterForm(request.POST,instance=debater)
        if form.is_valid():
            try:
               form.save()
            except ValueError:
                return render_to_response('error.html', 
                                         {'error_type': "Debater",
                                          'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"Debater name cannot be validated, most likely a non-existent debater"}, 
                                          context_instance=RequestContext(request))
            return render_to_response('thanks.html', 
                                     {'data_type': "Debater",
                                      'data_name': "["+form.cleaned_data['name']+"]"}, 
                                      context_instance=RequestContext(request))
    else:
        rounds = RoundStats.objects.filter(debater=debater)
        rounds = sorted(list(rounds), key=lambda x: x.round.round_number)
        form = DebaterForm(instance=debater)
        # Really only should be one
        teams = Team.objects.filter(debaters = debater)
        links = []
        for team in teams:
            links.append(('/team/'+str(team.id)+'/', "View %s"%team.name, False))

        return render_to_response('data_entry.html', 
                                 {'form': form,
                                  'debater_obj': debater,
                                  'links': links,
                                  'debater_rounds': rounds,
                                  'title':"Viewing Debater: %s"%(debater.name)}, 
                                  context_instance=RequestContext(request))

@permission_required('tab.debater.can_delete', login_url="/403/")    
def delete_debater(request, debater_id):
    error_msg = None
    try :
        debater_id = int(debater_id)
        d = Debater.objects.get(pk=debater_id)
        d.delete()
    except Debater.DoesNotExist:
        error_msg = "Can't delete a non-existent debater"
    except Exception as e:
        errors.emit_current_exception()
        error_msg = str(e)
    if error_msg:
        return render_to_response('error.html', 
                                 {'error_type': "Debater",
                                 'error_name': "Error Deleting Debater",
                                 'error_info':error_msg}, 
                                 context_instance=RequestContext(request))
    return render_to_response('thanks.html', 
                             {'data_type': "Debater",
                              'data_name': "["+str(debater_id)+"]",
                              'data_modification': 'DELETED'}, 
                              context_instance=RequestContext(request))
def enter_debater(request):
    if request.method == 'POST':
        form = DebaterForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return render_to_response('error.html', 
                                         {'error_type': "Debater",'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"Debater name cannot be validated, most likely a duplicate debater"}, 
                                          context_instance=RequestContext(request))
            return render_to_response('thanks.html', 
                                     {'data_type': "Debater",
                                      'data_name': "["+form.cleaned_data['name']+"]",
                                      'data_modification': "CREATED",
                                      'enter_again': True}, 
                                      context_instance=RequestContext(request))
    else:
        form = DebaterForm()
    return render_to_response('data_entry.html',
                             {'form': form,
                              'title': "Create Debater:"},
                              context_instance=RequestContext(request))

def rank_debaters_ajax(request):
    return render_to_response('rank_debaters.html',
                             {'title': "Debater Rankings"},
                              context_instance=RequestContext(request))

def rank_debaters(request):
    start_ms = int(round(time.time() * 1000))
    speakers = tab_logic.rank_speakers()
    debaters = [(s.debater,
                 s.speaks,
                 s.ranks,
                 s.debater.team_set.first()) for s in speakers]

    nov_debaters = filter(lambda s: s[0].novice_status == Debater.NOVICE,
            debaters)
    return render_to_response('rank_debaters_component.html', 
                             {'debaters': debaters, 
                              'nov_debaters' : nov_debaters,
                              'title': "Speaker Rankings"}, 
                             context_instance=RequestContext(request))

