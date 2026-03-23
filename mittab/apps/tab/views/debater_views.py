from django.shortcuts import render
from django.utils.text import slugify

from mittab.apps.tab.forms import DebaterForm
from mittab.apps.tab.helpers import redirect_and_flash_error, \
    redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs import tab_logic
from mittab.libs.cacheing import cache_logic
from mittab.libs.tab_logic import rankings
from mittab.libs.errors import *


def view_debaters(request):
    # Get a list of (id,debater_name) tuples
    c_debaters = [
        (
            debater.pk,
            debater.display,
            0,
            "",
            list(debater.ranking_groups.order_by("name").values_list("name", flat=True)),
        )
        for debater in Debater.objects.prefetch_related("ranking_groups").all()
    ]
    return render(
        request, "common/list_data.html", {
            "item_type": "debater",
            "title": "Viewing All Debaters",
            "item_list": c_debaters
        })


def view_debater(request, debater_id):
    debater_id = int(debater_id)
    try:
        debater = Debater.objects.prefetch_related("ranking_groups").get(pk=debater_id)
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
            updated_name = form.cleaned_data["name"]
            return redirect_and_flash_success(
                request, f"Debater {updated_name} updated successfully")
    else:
        rounds = RoundStats.objects.filter(debater=debater).select_related(
            "round__room", "round__chair").prefetch_related("round__judges")
        rounds = sorted(list(rounds), key=lambda x: x.round.round_number)
        form = DebaterForm(instance=debater)
        teams = Team.objects.filter(debaters=debater).select_related(
            "school", "hybrid_school"
        )
        links = []
        team_schools = []
        has_hybrid_school = False
        for team in teams:
            links.append(
                (f"/team/{team.id}/", f"View {team.name}"))
            school_info = {"team": team, "school": team.school}
            if team.hybrid_school:
                school_info["hybrid_school"] = team.hybrid_school
                has_hybrid_school = True
            team_schools.append(school_info)

        return render(
            request, "tab/debater_detail.html", {
                "form": form,
                "debater_obj": debater,
                "links": links,
                "debater_rounds": rounds,
                "team_schools": team_schools,
                "has_hybrid_school": has_hybrid_school,
                "title": f"Viewing Debater: {debater.name}"
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
            created_name = form.cleaned_data["name"]
            return redirect_and_flash_success(
                request,
                f"Debater {created_name} created successfully",
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

    ranking_group_tables = []
    ranking_groups = RankingGroup.objects.prefetch_related("debaters").order_by("name")
    for ranking_group in ranking_groups:
        debater_ids = {debater.id for debater in ranking_group.debaters.all()}
        grouped_debaters = [
            debater_entry for debater_entry in debaters
            if debater_entry[0].id in debater_ids
        ]
        if grouped_debaters:
            ranking_group_tables.append({
                "title": f"{ranking_group.name} Rankings",
                "anchor": f"debater-ranking-group-{slugify(ranking_group.name)}",
                "debaters": grouped_debaters,
            })

    return render(
        request, "tab/rank_debaters_component.html", {
            "debaters": debaters,
            "nov_debaters": nov_debaters,
            "debater_ranking_groups": ranking_group_tables,
            "title": "Speaker Rankings"
        })
