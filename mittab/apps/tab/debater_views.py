from django.shortcuts import render
from django.template import RequestContext
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.contrib.auth.decorators import permission_required
from mittab.apps.tab.forms import DebaterForm
from mittab.libs.errors import *
from mittab.apps.tab.helpers import redirect_and_flash_error, \
        redirect_and_flash_success
from mittab.apps.tab.models import *

from mittab.libs import tab_logic, errors


def view_debaters(request):
    #Get a list of (id,debater_name) tuples
    c_debaters = [(debater.pk, debater.name, 0, "")
                  for debater in Debater.objects.order_by("name")]
    return render(
        request, 'common/list_data.html', {
            'item_type': 'debater',
            'title': "Viewing All Debaters",
            'item_list': c_debaters
        })


def view_debater(request, debater_id):
    debater_id = int(debater_id)
    try:
        debater = Debater.objects.get(pk=debater_id)
    except Debater.DoesNotExist:
        return redirect_and_flash_error(request, "No such debater")
    if request.method == 'POST':
        form = DebaterForm(request.POST, instance=debater)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "Debater name cannot be validated, most likely a non-existent debater"
                )
            return redirect_and_flash_success(
                request, "Debater {} updated successfully".format(
                    form.cleaned_data['name']))
    else:
        rounds = RoundStats.objects.filter(debater=debater)
        rounds = sorted(list(rounds), key=lambda x: x.round.round_number)
        form = DebaterForm(instance=debater)
        # Really only should be one
        teams = Team.objects.filter(debaters=debater)
        links = []
        for team in teams:
            links.append(
                ('/team/' + str(team.id) + '/', "View %s" % team.name))

        return render(
            request, 'common/data_entry.html', {
                'form': form,
                'debater_obj': debater,
                'links': links,
                'debater_rounds': rounds,
                'title': "Viewing Debater: %s" % (debater.name)
            })


def enter_debater(request):
    if request.method == 'POST':
        form = DebaterForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "Debater name cannot be validated, most likely a duplicate debater"
                )
            return redirect_and_flash_success(
                request,
                "Debater {} created successfully".format(
                    form.cleaned_data['name']),
                path="/")
    else:
        form = DebaterForm()
    return render(request, 'common/data_entry.html', {
        'form': form,
        'title': "Create Debater:"
    })


def rank_debaters_ajax(request):
    return render(request, 'tab/rank_debaters.html',
                  {'title': "Debater Rankings"})


def rank_debaters(request):
    speakers = tab_logic.rank_speakers()
    debaters = [(s.debater, s.speaks, s.ranks, s.debater.team_set.first())
                for s in speakers]

    nov_debaters = filter(lambda s: s[0].novice_status == Debater.NOVICE,
                          debaters)
    return render(
        request, 'tab/rank_debaters_component.html', {
            'debaters': debaters,
            'nov_debaters': nov_debaters,
            'title': "Speaker Rankings"
        })
