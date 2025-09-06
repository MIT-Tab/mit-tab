from django.shortcuts import render

from mittab.apps.tab.forms import DebaterForm
from mittab.apps.tab.helpers import redirect_and_flash_error, \
    redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs import tab_logic, cache_logic
from mittab.libs.tab_logic import rankings
from mittab.libs.errors import *


def view_debaters(request):
    # Get a list of (id,debater_name) tuples
    c_debaters = [(debater.pk, debater.display, 0, "")
                  for debater in Debater.objects.all()]
    return render(
        request, "common/list_data.html", {
            "item_type": "debater",
            "title": "Viewing All Debaters",
            "item_list": c_debaters
        })


def view_debater(request, debater_id):
    debater_id = int(debater_id)
    try:
        debater = Debater.objects.get(pk=debater_id)
    except Debater.DoesNotExist:
        return redirect_and_flash_error(request, "No such debater")
    if request.method == "POST":
        form = DebaterForm(request.POST, instance=debater)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "Debater name can't be validated. Probably a non-existent debater"
                )
            return redirect_and_flash_success(
                request, "Debater {} updated successfully".format(
                    form.cleaned_data["name"]))
    else:
        rounds = RoundStats.objects.filter(debater=debater)
        rounds = sorted(list(rounds), key=lambda x: x.round.round_number)
        form = DebaterForm(instance=debater)
        # Really only should be one
        teams = Team.objects.filter(debaters=debater)
        links = []
        for team in teams:
            links.append(
                ("/team/" + str(team.id) + "/", "View %s" % team.name))

        return render(
            request, "common/data_entry.html", {
                "form": form,
                "debater_obj": debater,
                "links": links,
                "debater_rounds": rounds,
                "title": "Viewing Debater: %s" % (debater.name)
            })


def enter_debater(request):
    if request.method == "POST":
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
                    form.cleaned_data["name"]),
                path="/")
    else:
        form = DebaterForm()
    return render(request, "common/data_entry.html", {
        "form": form,
        "title": "Create Debater:"
    })


def rank_debaters_ajax(request):
    return render(request, "tab/rank_debaters.html",
                  {"title": "Debater Rankings"})


def get_speaker_rankings(request=None):
    speakers = tab_logic.rank_speakers()
    debaters = []
    for i, debater_stats in enumerate(speakers):
        tiebreaker = "N/A"
        if i != len(speakers) - 1:
            next_debater_stats = speakers[i + 1]
            tiebreaker_stat = debater_stats.get_tiebreaker(next_debater_stats)
            if tiebreaker_stat is not None:
                tiebreaker = tiebreaker_stat.name
            else:
                tiebreaker = "Tie not broken"
        debaters.append((debater_stats.debater, debater_stats[rankings.SPEAKS],
                         debater_stats[rankings.RANKS],
                         debater_stats.debater.team(), tiebreaker))

    nov_debaters = list(filter(lambda s: s[0].novice_status == Debater.NOVICE,
                               debaters))

    return debaters, nov_debaters


def rank_debaters(request):
    debaters, nov_debaters = cache_logic.cache_fxn_key(
        get_speaker_rankings,
        "speaker_rankings",
        cache_logic.DEFAULT,
        request
    )

    return render(
        request, "tab/rank_debaters_component.html", {
            "debaters": debaters,
            "nov_debaters": nov_debaters,
            "title": "Speaker Rankings"
        })
