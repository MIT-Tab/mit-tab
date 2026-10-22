from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.utils.text import slugify

from mittab.apps.tab.auth_roles import (
    CAP_ADD_SCRATCHES,
    CAP_CHECKINS,
    CAP_DATA_ENTRY,
    CAP_VIEW_SCRATCHES,
    is_apda_board_user,
    user_has_staff_capability,
)
from mittab.apps.tab.forms import TeamForm, TeamEntryForm, ScratchForm
from mittab.apps.tab.views.board_views import tab_cards as board_tab_cards
from mittab.libs.cacheing import cache_logic
from mittab.libs.errors import *
from mittab.apps.tab.helpers import redirect_and_flash_error, \
    redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs import tab_logic
from mittab.libs.tab_logic import TabFlags
from mittab.apps.tab.tab_cards import get_tab_cards
from mittab.libs.tab_logic import rankings




def view_teams(request):
    include_checkins = user_has_staff_capability(request.user, CAP_CHECKINS)

    def flags(team):
        if not include_checkins:
            return 0
        result = 0
        if team.checked_in:
            result |= TabFlags.TEAM_CHECKED_IN
        else:
            result |= TabFlags.TEAM_NOT_CHECKED_IN
        return result

    c_teams = [
        (
            team.id,
            team.display_backend,
            flags(team),
            TabFlags.flags_to_symbols(flags(team)),
            list(team.ranking_groups.order_by("name").values_list("name", flat=True)),
        )
        for team in Team.objects.prefetch_related("ranking_groups").all()
    ]
    filters, symbol_text = [], ""
    if include_checkins:
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
        team = Team.with_preloaded_relations_for_tabbing().get(pk=team_id)
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
        form = TeamForm(
            request.POST,
            instance=team,
            allow_checkins=user_has_staff_capability(request.user, CAP_CHECKINS),
        )
        if form.is_valid():
            try:
                form.save(actor=request.user)
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "An error occured, most likely a non-existent team")
            updated_name = form.cleaned_data["name"]
            return redirect_and_flash_success(
                request, f"Team {updated_name} updated successfully")
    else:
        form = TeamForm(
            instance=team,
            allow_checkins=user_has_staff_capability(request.user, CAP_CHECKINS),
        )
        links = []
        if user_has_staff_capability(request.user, CAP_DATA_ENTRY):
            links.append((f"/team/{team_id}/delete/", "Delete"))
        if user_has_staff_capability(request.user, CAP_VIEW_SCRATCHES):
            links.append((
                f"/team/{team_id}/scratches/view/",
                f"Scratches for {team.display_backend}",
            ))
        return render(
            request, "tab/team_detail.html", {
                "title": f"Viewing Team: {team.display_backend}",
                "form": form,
                "links": links,
                "team_obj": team,
                "team_stats": stats,
                "audit_events": team.audit_events.all(),
            })

    return render(request, "common/data_entry.html", {"form": form})


def enter_team(request):
    allow_initial_scratches = (
        user_has_staff_capability(request.user, CAP_ADD_SCRATCHES) or
        user_has_staff_capability(request.user, CAP_VIEW_SCRATCHES)
    )
    allow_checkins = user_has_staff_capability(request.user, CAP_CHECKINS)
    if request.method == "POST":
        form = TeamEntryForm(
            request.POST,
            allow_initial_scratches=allow_initial_scratches,
            allow_checkins=allow_checkins,
        )
        if form.is_valid():
            try:
                team = form.save(actor=request.user)
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "Team name cannot be validated, most likely a duplicate school"
                )
            num_forms = form.cleaned_data.get("number_scratches", 0)
            if num_forms > 0:
                return HttpResponseRedirect(
                    f"/team/{team.pk}/scratches/add/{num_forms}"
                )
            else:
                team_name = team.display_backend
                return redirect_and_flash_success(
                    request,
                    f"Team {team_name} created successfully",
                    path="/")
    else:
        form = TeamEntryForm(
            allow_initial_scratches=allow_initial_scratches,
            allow_checkins=allow_checkins,
        )
    return render(request, "common/data_entry.html", {
        "form": form,
        "title": "Create Team"
    })


def delete_team(request, team_id):
    if not user_has_staff_capability(request.user, CAP_DATA_ENTRY):
        return redirect_and_flash_error(request, "You cannot delete teams", path="/403/")

    try:
        team_id = int(team_id)
        team = Team.objects.get(pk=team_id)
        team.delete()
    except Team.DoesNotExist:
        return redirect_and_flash_error(request, "That team does not exist")
    except Exception as exc:
        return redirect_and_flash_error(request, str(exc))
    return redirect_and_flash_success(request,
                                      "Team deleted successfully",
                                      path="/")


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
    all_teams = Team.objects.all()
    all_judges = Judge.objects.all()
    if request.method == "POST":
        forms = [
            ScratchForm(
                request.POST,
                prefix=str(
                    i + 1),
                team_queryset=all_teams,
                judge_queryset=all_judges)
            for i in range(number_scratches)
        ]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            for form in forms:
                form.save(actor=request.user)
            return redirect_and_flash_success(
                request, "Scratches created successfully")
    else:
        forms = [
            ScratchForm(
                prefix=str(i),
                initial={
                    "team": team_id,
                    "scratch_type": 0
                },
                team_queryset=all_teams,
                judge_queryset=all_judges
            ) for i in range(1, number_scratches + 1)
        ]
    return render(
        request, "common/data_entry_multiple.html", {
            "forms": list(zip(forms, [None] * len(forms), [None] * len(forms))),
            "data_type": "Scratch",
            "title": f"Adding Scratch(es) for {team.display_backend}"
        })


def view_scratches(request, team_id):
    try:
        team_id = int(team_id)
    except ValueError:
        return redirect_and_flash_error(request, "Received invalid data")
    scratches = Scratch.objects.filter(team=team_id)
    number_scratches = len(scratches)
    team = Team.objects.get(pk=team_id)
    all_teams = Team.objects.all()
    all_judges = Judge.objects.all()
    if request.method == "POST":
        forms = [
            ScratchForm(
                request.POST,
                prefix=str(i + 1),
                instance=scratches[i],
                team_queryset=all_teams,
                judge_queryset=all_judges
            )
            for i in range(number_scratches)
        ]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            for form in forms:
                form.save(actor=request.user)
            return redirect_and_flash_success(
                request, "Scratches successfully modified")
    else:
        forms = [
            ScratchForm(
                prefix=str(i + 1),
                instance=scratches[i],
                team_queryset=all_teams,
                judge_queryset=all_judges
            )
            for i in range(len(scratches))
        ]
    read_only = not request.user.is_superuser
    delete_links = [None] * len(scratches)
    if not read_only:
        delete_links = [
            f"/team/{team_id}/scratches/delete/{scratches[i].id}"
            for i in range(len(scratches))
        ]
    metadata = [
        {
            "created_by": scratch.created_by_display,
            "created_at": scratch.created_at,
            "audit_events": scratch.audit_events.all(),
        }
        for scratch in scratches
    ]
    links = [(f"/team/{team_id}/scratches/add/1/", "Add Scratch")]
    return render(
        request, "common/data_entry_multiple.html", {
            "forms": list(zip(forms, delete_links, metadata)),
            "data_type": "Scratch",
            "links": links,
            "read_only": read_only,
            "title": f"Viewing Scratch Information for {team.display_backend}"
        })


@never_cache
def all_tab_cards(request):
    if is_apda_board_user(request.user):
        return board_tab_cards(request)
    if not request.user.has_perm("tab.tab_settings.can_change"):
        return HttpResponseRedirect("/403/")

    return render(request, "tab/all_tab_cards.html", {"cards": get_tab_cards()})


@never_cache
def pretty_tab_card(request, team_id):
    if is_apda_board_user(request.user):
        return board_tab_cards(request, team_id=team_id)
    cards = get_tab_cards(team_id)
    return render(request, "tab/pretty_tab_card.html", {
        "team": get_object_or_404(Team, pk=team_id),
        "card": cards[0] if cards else None,
    })


@never_cache
def tab_card(request, team_id):
    if is_apda_board_user(request.user):
        return board_tab_cards(request, team_id=team_id)
    cards = get_tab_cards(team_id)
    return render(request, "tab/tab_card.html", {
        "card": cards[0] if cards else None,
    })


def rank_teams_ajax(request):
    return render(request, "tab/rank_teams.html", {"title": "Team Rankings"})

def rank_teams(request):
    teams, nov_teams = cache_logic.cache_fxn_key(
        rankings.get_team_rankings,
        "team_rankings_private",
        cache_logic.DEFAULT,
        request,
        public=False
    )

    ranking_group_tables = []
    ranking_groups = RankingGroup.objects.prefetch_related("teams").order_by("name")
    for ranking_group in ranking_groups:
        team_ids = {team.id for team in ranking_group.teams.all()}
        grouped_teams = [team for team in teams if team[0].id in team_ids]
        if grouped_teams:
            ranking_group_tables.append({
                "title": f"{ranking_group.name} Rankings",
                "anchor": f"team-ranking-group-{slugify(ranking_group.name)}",
                "teams": grouped_teams,
            })

    return render(request, "tab/rank_teams_component.html", {
        "varsity": teams,
        "novice": nov_teams,
        "team_ranking_groups": ranking_group_tables,
        "title": "Team Rankings"
    })
