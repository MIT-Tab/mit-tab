from django.shortcuts import render
from django.contrib.auth.decorators import permission_required
from forms import DebaterForm
from errors import *
from models import *
import mittab.libs.tab_logic as tab_logic

def view_debaters(request):
    #Get a list of (id,debater_name) tuples
    c_debaters = [(debater.pk,debater.name) for debater in Debater.objects.order_by("name")]
    return render(request, 'list_data.html', {'item_type':'debater',
                                              'title': "Viewing All Debaters",
                                              'item_list':c_debaters})

def view_debater(request, debater_id):
    debater_id = int(debater_id)
    try:
        debater = Debater.objects.get(pk=debater_id)
    except Debater.DoesNotExist:
        return render(request, 'error.html', {'error_type': "View Debater",
                                              'error_name': str(debater_id),
                                              'error_info':"No such debater"})
    if request.method == 'POST':
        form = DebaterForm(request.POST,instance=debater)
        if form.is_valid():
            try:
               form.save()
            except ValueError:
                return render(request, 'error.html', {'error_type': "Debater",
                                                      'error_name': "["+form.cleaned_data['name']+"]",
                                                      'error_info':"Debater name cannot be validated, most likely a non-existent debater"})

            return render(request, 'thanks.html', {'data_type': "Debater",
                                                   'data_name': "["+form.cleaned_data['name']+"]"})
    else:
        rounds = RoundStats.objects.filter(debater=debater)
        rounds = sorted(list(rounds), key=lambda x: x.round.round_number)
        form = DebaterForm(instance=debater)
        # Really only should be one, TODO: change to get when we have tests
        teams = Team.objects.filter(debaters = debater)
        links = [('/debater/'+str(debater_id)+'/delete/', 'Delete', True)]
        for team in teams:
            links.append(('/team/'+str(team.id)+'/', "View %s"%team.name, False))

        return render(request, 'data_entry.html',
                               {'form': form,
                                'debater_obj': debater,
                                'links': links,
                                'debater_rounds': rounds,
                                'title':"Viewing Debater: %s"%(debater.name)})

@permission_required('tab.debater.can_delete', login_url="/403/")
def delete_debater(request, debater_id):
    error_msg = None
    try :
        debater_id = int(debater_id)
        d = Debater.objects.get(pk=debater_id)
        d.delete()
    except Debater.DoesNotExist:
        error_msg = "Can't delete a non-existent debater"
    except Exception, e:
        error_msg = str(e)
    if error_msg:
        return render(request, 'error.html',
                               {'error_type': "Debater",
                                'error_name': "Error Deleting Debater",
                                'error_info':error_msg})
    return render(request, 'thanks.html',
                            {'data_type': "Debater",
                             'data_name': "["+str(debater_id)+"]",
                             'data_modification': 'DELETED'})
def enter_debater(request):
    if request.method == 'POST':
        form = DebaterForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return render(request, 'error.html',
                                       {'error_type': "Debater",'error_name': "["+form.cleaned_data['name']+"]",
                                        'error_info':"Debater name cannot be validated, most likely a duplicate debater"})
            return render(request, 'thanks.html',
                                   {'data_type': "Debater",
                                    'data_name': "["+form.cleaned_data['name']+"]",
                                    'data_modification': "CREATED",
                                    'enter_again': True})
    else:
        form = DebaterForm()
    return render(request, 'data_entry.html',
                            {'form': form,
                             'title': "Create Debater:"})

def rank_debaters_ajax(request):
    return render(request, 'rank_debaters.html', {'title': "Debater Rankings"})

def rank_debaters(request):
    speakers = tab_logic.rank_speakers()
    debaters = [(s,
                 tab_logic.tot_speaks_deb(s),
                 tab_logic.tot_ranks_deb(s),
                 tab_logic.deb_team(s)) for s in speakers]

    nov_speakers = tab_logic.rank_nov_speakers()
    nov_debaters = [(s,
                     tab_logic.tot_speaks_deb(s),
                     tab_logic.tot_ranks_deb(s),
                     tab_logic.deb_team(s)) for s in nov_speakers]

    return render(request, 'rank_debaters_component.html', {'debaters': debaters,
                                                            'nov_debaters': nov_debaters,
                                                            'title': "Speaker Rankings"})

