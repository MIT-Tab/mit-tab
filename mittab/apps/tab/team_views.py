from django.http import HttpResponseRedirect, JsonResponse
from django.contrib.auth.decorators import permission_required
from django.shortcuts import render

from mittab.apps.tab.forms import TeamForm, TeamEntryForm, ScratchForm
from mittab.libs.errors import *
from mittab.apps.tab.helpers import redirect_and_flash_error, \
        redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs import tab_logic, cache_logic
from mittab.libs.tab_logic import TabFlags, tot_speaks_deb, \
        tot_ranks_deb, tot_speaks, tot_ranks
from mittab.libs.tab_logic import rankings


def public_view_teams(request):
    display_teams = TabSettings.get("teams_public", 0)

    if not request.user.is_authenticated and not display_teams:
        return redirect_and_flash_error(request, "This view is not public", path="/")

    return render(
        request, "public/teams.html", {
            "teams": Team.objects.order_by("-checked_in",
                                           "school__name").all(),
            "num_checked_in": Team.objects.filter(checked_in=True).count()
        })


def view_teams(request):
    def flags(team):
        result = 0
        if team.checked_in:
            result |= TabFlags.TEAM_CHECKED_IN
        else:
            result |= TabFlags.TEAM_NOT_CHECKED_IN
        return result

    c_teams = [(team.id, team.display_backend, flags(team),
                TabFlags.flags_to_symbols(flags(team)))
               for team in Team.objects.all()]
    all_flags = [[TabFlags.TEAM_CHECKED_IN, TabFlags.TEAM_NOT_CHECKED_IN]]
    filters, symbol_text = TabFlags.get_filters_and_symbols(all_flags)
    return render(
        request, "common/list_data.html", {
            "item_type": "team",
            "title": "Viewing All Teams",
            "item_list": c_teams,
            "filters": filters,
            "symbol_text": symbol_text
        })


def view_team(request, team_id):
    team_id = int(team_id)
    try:
        team = Team.objects.get(pk=team_id)
        stats = []
        stats.append(("Wins", tab_logic.tot_wins(team)))
        stats.append(("Total Speaks", tab_logic.tot_speaks(team)))
        stats.append(("Govs", tab_logic.num_govs(team)))
        stats.append(("Opps", tab_logic.num_opps(team)))
        stats.append(("Avg. Opp Wins", tab_logic.opp_strength(team)))
        stats.append(("Been Pullup", tab_logic.pull_up_count(team)))
        stats.append(("Hit Pullup", tab_logic.hit_pull_up(team)))
    except Team.DoesNotExist:
        return redirect_and_flash_error(request, "Team not found")
    if request.method == "POST":
        form = TeamForm(request.POST, instance=team)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "An error occured, most likely a non-existent team")
            return redirect_and_flash_success(
                request, "Team {} updated successfully".format(
                    form.cleaned_data["name"]))
    else:
        form = TeamForm(instance=team)
        links = [("/team/" + str(team_id) + "/scratches/view/",
                  "Scratches for {}".format(team.display_backend))]
        for deb in team.debaters.all():
            links.append(
                ("/debater/" + str(deb.id) + "/", "View %s" % deb.name))
        return render(
            request, "common/data_entry.html", {
                "title": "Viewing Team: %s" % (team.display_backend),
                "form": form,
                "links": links,
                "team_obj": team,
                "team_stats": stats
            })

    return render(request, "common/data_entry.html", {"form": form})


def enter_team(request):
    if request.method == "POST":
        form = TeamEntryForm(request.POST)
        if form.is_valid():
            try:
                team = form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "Team name cannot be validated, most likely a duplicate school"
                )
            num_forms = form.cleaned_data["number_scratches"]
            if num_forms > 0:
                return HttpResponseRedirect("/team/" + str(team.pk) +
                                            "/scratches/add/" + str(num_forms))
            else:
                return redirect_and_flash_success(
                    request,
                    "Team {} created successfully".format(team.display_backend),
                    path="/")
    else:
        form = TeamEntryForm()
    return render(request, "common/data_entry.html", {
        "form": form,
        "title": "Create Team"
    })


def add_scratches(request, team_id, number_scratches):
    try:
        team_id, number_scratches = int(team_id), int(number_scratches)
    except ValueError:
        return redirect_and_flash_error(request, "Received invalid data")
    try:
        team = Team.objects.get(pk=team_id)
    except Team.DoesNotExist:
        return redirect_and_flash_error(request,
                                        "The selected team does not exist")
    if request.method == "POST":
        forms = [
            ScratchForm(request.POST, prefix=str(i))
            for i in range(1, number_scratches + 1)
        ]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            for form in forms:
                form.save()
            return redirect_and_flash_success(
                request, "Scratches created successfully")
    else:
        forms = [
            ScratchForm(prefix=str(i),
                        initial={
                            "team": team_id,
                            "scratch_type": 0
                        }) for i in range(1, number_scratches + 1)
        ]
    return render(
        request, "common/data_entry_multiple.html", {
            "forms": list(zip(forms, [None] * len(forms))),
            "data_type": "Scratch",
            "title": "Adding Scratch(es) for %s" % (team.display_backend)
        })


def view_scratches(request, team_id):
    try:
        team_id = int(team_id)
    except ValueError:
        return redirect_and_flash_error(request, "Received invalid data")
    scratches = Scratch.objects.filter(team=team_id)
    number_scratches = len(scratches)
    team = Team.objects.get(pk=team_id)
    if request.method == "POST":
        forms = [
            ScratchForm(request.POST, prefix=str(i), instance=scratches[i - 1])
            for i in range(1, number_scratches + 1)
        ]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            for form in forms:
                form.save()
            return redirect_and_flash_success(
                request, "Scratches successfully modified")
    else:
        forms = [
            ScratchForm(prefix=str(i), instance=scratches[i - 1])
            for i in range(1,
                           len(scratches) + 1)
        ]
    delete_links = [
        "/team/" + str(team_id) + "/scratches/delete/" + str(scratches[i].id)
        for i in range(len(scratches))
    ]
    links = [("/team/" + str(team_id) + "/scratches/add/1/", "Add Scratch")]
    return render(
        request, "common/data_entry_multiple.html", {
            "forms": list(zip(forms, delete_links)),
            "data_type": "Scratch",
            "links": links,
            "title": "Viewing Scratch Information for %s" % (team.display_backend)
        })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def all_tab_cards(request):
    all_teams = Team.objects.all()
    return render(request, "tab/all_tab_cards.html", locals())


def pretty_tab_card(request, team_id):
    try:
        team_id = int(team_id)
    except Exception:
        return redirect_and_flash_error(request, "Invalid team id")
    team = Team.objects.get(pk=team_id)
    return render(request, "tab/pretty_tab_card.html", {"team": team})


def tab_card(request, team_id):
    try:
        team_id = int(team_id)
    except ValueError:
        return redirect_and_flash_error(request, "Invalid team id")
    team = Team.objects.get(pk=team_id)
    rounds = ([r for r in Round.objects.filter(gov_team=team)] +
              [r for r in Round.objects.filter(opp_team=team)])
    rounds.sort(key=lambda x: x.round_number)
    debaters = [d for d in team.debaters.all()]
    iron_man = False
    if len(debaters) == 1:
        iron_man = True
    deb1 = debaters[0]
    if not iron_man:
        deb2 = debaters[1]
    round_stats = []
    num_rounds = TabSettings.objects.get(key="tot_rounds").value
    cur_round = TabSettings.objects.get(key="cur_round").value
    blank = " "
    for i in range(num_rounds):
        round_stats.append([blank] * 7)

    for round_obj in rounds:
        dstat1 = [
            k for k in RoundStats.objects.filter(debater=deb1).filter(
                round=round_obj).all()
        ]
        dstat2 = []
        if not iron_man:
            dstat2 = [
                k for k in RoundStats.objects.filter(debater=deb2).filter(
                    round=round_obj).all()
            ]
        blank_rs = RoundStats(debater=deb1, round=round_obj, speaks=0, ranks=0)
        while len(dstat1) + len(dstat2) < 2:
            # Something is wrong with our data, but we don't want to crash
            dstat1.append(blank_rs)
        if not dstat2 and not dstat1:
            break
        if not dstat2:
            dstat1, dstat2 = dstat1[0], dstat1[1]
        elif not dstat1:
            dstat1, dstat2 = dstat2[0], dstat2[1]
        else:
            dstat1, dstat2 = dstat1[0], dstat2[0]
        index = round_obj.round_number - 1
        round_stats[index][3] = " - ".join(
            [j.name for j in round_obj.judges.all()])
        round_stats[index][4] = (float(dstat1.speaks), float(dstat1.ranks))
        round_stats[index][5] = (float(dstat2.speaks), float(dstat2.ranks))
        round_stats[index][6] = (float(dstat1.speaks + dstat2.speaks),
                                 float(dstat1.ranks + dstat2.ranks))

        if round_obj.gov_team == team:
            round_stats[index][2] = round_obj.opp_team
            round_stats[index][0] = "G"
            if round_obj.victor == 1:
                round_stats[index][1] = "W"
            elif round_obj.victor == 2:
                round_stats[index][1] = "L"
            elif round_obj.victor == 3:
                round_stats[index][1] = "WF"
            elif round_obj.victor == 4:
                round_stats[index][1] = "LF"
            elif round_obj.victor == 5:
                round_stats[index][1] = "AD"
            elif round_obj.victor == 6:
                round_stats[index][1] = "AW"
        elif round_obj.opp_team == team:
            round_stats[index][2] = round_obj.gov_team
            round_stats[index][0] = "O"
            if round_obj.victor == 1:
                round_stats[index][1] = "L"
            elif round_obj.victor == 2:
                round_stats[index][1] = "W"
            elif round_obj.victor == 3:
                round_stats[index][1] = "LF"
            elif round_obj.victor == 4:
                round_stats[index][1] = "WF"
            elif round_obj.victor == 5:
                round_stats[index][1] = "AD"
            elif round_obj.victor == 6:
                round_stats[index][1] = "AW"

    for i in range(cur_round - 1):
        if round_stats[i][6] == blank:
            round_stats[i][6] = (0, 0)
    for i in range(1, cur_round - 1):
        round_stats[i][6] = (round_stats[i][6][0] + round_stats[i - 1][6][0],
                             round_stats[i][6][1] + round_stats[i - 1][6][1])
    #Error out if we don't have a bye
    try:
        bye_round = Bye.objects.get(bye_team=team).round_number
    except Exception:
        bye_round = None

    #Duplicates Debater 1 for display if Ironman team
    if iron_man:
        deb2 = deb1
    return render(
        request, "tab/tab_card.html", {
            "team_name": team.display_backend,
            "team_school": team.school,
            "debater_1": deb1.name,
            "debater_1_status": Debater.NOVICE_CHOICES[deb1.novice_status][1],
            "debater_2": deb2.name,
            "debater_2_status": Debater.NOVICE_CHOICES[deb2.novice_status][1],
            "round_stats": round_stats,
            "d1st": tot_speaks_deb(deb1),
            "d1rt": tot_ranks_deb(deb1),
            "d2st": tot_speaks_deb(deb2),
            "d2rt": tot_ranks_deb(deb2),
            "ts": tot_speaks(team),
            "tr": tot_ranks(team),
            "bye_round": bye_round
        })


def rank_teams_ajax(request):
    return render(request, "tab/rank_teams.html", {"title": "Team Rankings"})


def get_team_rankings(request):
    ranked_teams = tab_logic.rankings.rank_teams()
    teams = []
    for i, team_stat in enumerate(ranked_teams):
        tiebreaker = "N/A"
        if i != len(ranked_teams) - 1:
            next_team_stat = ranked_teams[i + 1]
            tiebreaker_stat = team_stat.get_tiebreaker(next_team_stat)
            if tiebreaker_stat is not None:
                tiebreaker = tiebreaker_stat.name
            else:
                tiebreaker = "Tie not broken"
        teams.append((team_stat.team, team_stat[rankings.WINS],
                      team_stat[rankings.SPEAKS], team_stat[rankings.RANKS],
                      tiebreaker))

    nov_teams = list(filter(
        lambda ts: all(
            map(lambda d: d.novice_status == Debater.NOVICE, ts[0].debaters.
                all())), teams))

    return teams, nov_teams


def rank_teams(request):
    teams, nov_teams = cache_logic.cache_fxn_key(
        get_team_rankings,
        "team_rankings",
        cache_logic.DEFAULT,
        request
    )

    return render(request, "tab/rank_teams_component.html", {
        "varsity": teams,
        "novice": nov_teams,
        "title": "Team Rankings"
    })


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

        if hasattr(team, "breaking_team"):
            stats["outround_seed"] = team.breaking_team.seed
            stats["effective_outround_seed"] = team.breaking_team.effective_seed

        data = {"success": True, "result": stats}
    except Team.DoesNotExist:
        data = {"success": False}
    return JsonResponse(data)
