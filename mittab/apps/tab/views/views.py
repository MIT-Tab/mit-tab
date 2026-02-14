import os
from django.db import IntegrityError
from django.db.models import Q
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth import logout
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, reverse
from django.core.management import call_command
import yaml

from mittab.apps.tab.archive import ArchiveExporter
from mittab.apps.tab.views.debater_views import get_speaker_rankings
from mittab.apps.tab.forms import MiniRoomTagForm, RoomTagForm, SchoolForm, RoomForm, \
    UploadDataForm, ScratchForm, SettingsForm, PublicHomeShortcutsForm
from mittab.apps.tab.helpers import redirect_and_flash_error, \
    redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.apps.tab.views.outround_pairing_views import create_forum_view_data
from mittab.apps.tab.public_rankings import (
    get_all_ballot_round_settings,
    get_all_ranking_settings,
    get_all_standings_publication_settings,
    get_paired_round_numbers,
    set_ballot_round_settings,
    set_ranking_settings,
    set_standings_publication_setting,
)
from mittab.libs.cacheing import cache_logic
from mittab.libs.cacheing.public_cache import (
    invalidate_all_public_caches,
    invalidate_public_rankings_cache,
)
from mittab.libs.tab_logic import TabFlags
from mittab.libs.data_import import import_judges, import_rooms, import_teams, \
    import_scratches
from mittab.libs.tab_logic.rankings import get_team_rankings


def index(request):
    if not request.user.is_authenticated:
        return redirect("public_home")

    schools_missing_apda_qs = School.objects.filter(
        Q(apda_id__isnull=True) | Q(apda_id=-1)
    )
    debaters_missing_apda_qs = Debater.objects.filter(
        Q(apda_id__isnull=True) | Q(apda_id=-1)
    )

    school_list = [(school.pk, school.name) for school in School.objects.all()]
    judge_list = [(judge.pk, judge.name) for judge in Judge.objects.all()]
    team_list = [(team.pk, team.display_backend) for team in Team.objects.all()]
    debater_list = [(debater.pk, debater.display)
                    for debater in Debater.objects.all()]
    room_list = [(room.pk, room.name) for room in Room.objects.all()]
    expected_finals = 1+ bool(TabSettings.get("nov_teams_to_break", 4))
    completed_finals = Outround.objects.filter(num_teams=2).exclude(
        victor=Outround.UNKNOWN
    ).count()

    number_teams = len(team_list)
    number_judges = len(judge_list)
    number_schools = len(school_list)
    number_debaters = len(debater_list)
    number_rooms = len(room_list)

    context = {
        "school_list": school_list,
        "judge_list": judge_list,
        "team_list": team_list,
        "debater_list": debater_list,
        "room_list": room_list,
        "expected_finals": expected_finals,
        "completed_finals": completed_finals,
        "number_teams": number_teams,
        "number_judges": number_judges,
        "number_schools": number_schools,
        "number_debaters": number_debaters,
        "number_rooms": number_rooms,
        "schools_missing_apda_ids": [
            school.pk for school in schools_missing_apda_qs
        ],
        "debaters_missing_apda_ids": [
            debater.pk for debater in debaters_missing_apda_qs
        ],
        "schools_missing_apda_count": schools_missing_apda_qs.count(),
        "debaters_missing_apda_count": debaters_missing_apda_qs.count(),
    }

    return render(request, "common/index.html", context)


def tab_logout(request, *args):
    logout(request)
    return redirect_and_flash_success(request,
                                      "Successfully logged out",
                                      path="/")


def render_403(request, *args, **kwargs):
    response = render(request, "common/403.html")
    response.status_code = 403
    return response


def render_404(request, *args, **kwargs):
    response = render(request, "common/404.html")
    response.status_code = 404
    return response


def render_500(request, *args, **kwargs):
    response = render(request, "common/500.html")
    response.status_code = 500
    return response


# View for manually adding scratches
def add_scratch(request):
    if request.method == "POST":
        form = ScratchForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except IntegrityError:
                return redirect_and_flash_error(request,
                                                "This scratch already exists.")
        return redirect_and_flash_success(request,
                                          "Scratch created successfully")
    else:
        form = ScratchForm(initial={"scratch_type": 0})
    return render(request, "common/data_entry.html", {
        "title": "Adding Scratch",
        "form": form
    })


#### BEGIN SCHOOL ###
# Three views for entering, viewing, and editing schools
def view_schools(request):
    # Get a list of (id,school_name) tuples
    c_schools = [(s.pk, s.name, 0, "") for s in School.objects.all()]
    return render(
        request, "common/list_data.html", {
            "item_type": "school",
            "title": "Viewing All Schools",
            "item_list": c_schools
        })


def view_school(request, school_id):
    school_id = int(school_id)
    try:
        school = School.objects.get(pk=school_id)
    except School.DoesNotExist:
        return redirect_and_flash_error(request, "School not found")
    if request.method == "POST":
        form = SchoolForm(request.POST, instance=school)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "School name cannot be validated, most likely a non-existent school"
                )
            updated_name = form.cleaned_data["name"]
            return redirect_and_flash_success(
                request, f"School {updated_name} updated successfully")
    else:
        form = SchoolForm(instance=school)
        links = [(f"/school/{school_id}/delete/", "Delete")]

        teams = Team.objects.filter(school=school).prefetch_related("debaters")
        hybrid_teams = Team.objects.filter(
            hybrid_school=school
        ).prefetch_related("debaters")
        judges = Judge.objects.filter(schools=school)

        return render(
            request, "tab/school_detail.html", {
                "form": form,
                "links": links,
                "school_teams": teams,
                "school_hybrid_teams": hybrid_teams,
                "school_judges": judges,
                "title": f"Viewing School: {school.name}"
            })


def enter_school(request):
    if request.method == "POST":
        form = SchoolForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "School name cannot be validated, most likely a duplicate school"
                )
            created_name = form.cleaned_data["name"]
            return redirect_and_flash_success(
                request,
                f"School {created_name} created successfully",
                path="/")
    else:
        form = SchoolForm()
    return render(request, "common/data_entry.html", {
        "form": form,
        "title": "Create School"
    })


@permission_required("tab.school.can_delete", login_url="/403/")
def delete_school(request, school_id):
    error_msg = None
    try:
        school_id = int(school_id)
        school = School.objects.get(pk=school_id)
        school.delete()
    except School.DoesNotExist:
        error_msg = "That school does not exist"
    except Exception as e:
        error_msg = str(e)
    if error_msg:
        return redirect_and_flash_error(request, error_msg)
    return redirect_and_flash_success(request,
                                      "School deleted successfully",
                                      path="/")


#### END SCHOOL ###


#### BEGIN ROOM ###
def view_rooms(request):
    def flags(room):
        result = 0
        if room.rank == 0:
            result |= TabFlags.ROOM_ZERO_RANK
        else:
            result |= TabFlags.ROOM_NON_ZERO_RANK
        return result

    all_flags = [[TabFlags.ROOM_ZERO_RANK, TabFlags.ROOM_NON_ZERO_RANK]]
    all_rooms = [(room.pk, room.name, flags(room),
                  TabFlags.flags_to_symbols(flags(room)))
                 for room in Room.objects.all()]
    filters, symbol_text = TabFlags.get_filters_and_symbols(all_flags)
    return render(
        request, "common/list_data.html", {
            "item_type": "room",
            "title": "Viewing All Rooms",
            "item_list": all_rooms,
            "symbol_text": symbol_text,
            "filters": filters
        })


def view_room(request, room_id):
    rounds = []
    outrounds = []
    room_id = int(room_id)
    try:
        room = Room.objects.get(pk=room_id)
    except Room.DoesNotExist:
        return redirect_and_flash_error(request, "Room not found")
    if request.method == "POST":
        form = RoomForm(request.POST, instance=room)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "Room name cannot be validated, most likely a non-existent room"
                )
            updated_name = form.cleaned_data["name"]
            return redirect_and_flash_success(
                request, f"School {updated_name} updated successfully")
    else:
        form = RoomForm(instance=room)

        # Get all rounds that happened in this room with related judges
        rounds = Round.objects.filter(room=room).select_related(
            "gov_team", "opp_team", "chair").prefetch_related("judges")

        # Get all outrounds that happened in this room with related judges
        outrounds = Outround.objects.filter(room=room).select_related(
            "gov_team", "opp_team", "chair").prefetch_related("judges")

    return render(request, "tab/room_detail.html", {
        "form": form,
        "links": [],
        "room_rounds": rounds,
        "room_outrounds": outrounds,
        "title": f"Viewing Room: {room.name}"
    })


def enter_room(request):
    if request.method == "POST":
        form = RoomForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request,
                    "Room name cannot be validated, most likely a duplicate room"
                )
            created_name = form.cleaned_data["name"]
            return redirect_and_flash_success(
                request,
                f"Room {created_name} created successfully",
                path="/")
    else:
        form = RoomForm()
    return render(request, "common/data_entry.html", {
        "form": form,
        "title": "Create Room"
    })


def bulk_check_in(request):
    entity_type = request.POST.get("entity_type")
    action = request.POST.get("action")

    entity_ids = request.POST.getlist("entity_ids[]")
    entity_ids = [int(eid) for eid in entity_ids if eid.isdigit()]

    if not entity_ids:
        return JsonResponse({"success": True})

    # Teams have a simple boolean field
    if entity_type == "team":
        Team.objects.filter(pk__in=entity_ids).update(checked_in=action == "check_in")
        return JsonResponse({"success": True})

    # Judges and rooms use check-in records per round
    round_numbers = [int(rn) for rn in request.POST.getlist("rounds[]") if rn.isdigit()]

    if not round_numbers:
        return JsonResponse({"success": True})

    if entity_type == "judge":
        checkInObj, id_field = CheckIn, "judge_id"
    else:
        checkInObj, id_field = RoomCheckIn, "room_id"

    if action == "check_in":
        checkInObj.objects.bulk_create(
            [checkInObj(**{id_field: eid, "round_number": rn})
             for eid in entity_ids for rn in round_numbers],
            ignore_conflicts=True
        )
    else:
        checkInObj.objects.filter(**{f"{id_field}__in": entity_ids},
                                  round_number__in=round_numbers).delete()

    return JsonResponse({"success": True})


@permission_required("tab.scratch.can_delete", login_url="/403/")
def delete_scratch(request, _item_id, scratch_id):
    try:
        scratch_id = int(scratch_id)
        scratch = Scratch.objects.get(pk=scratch_id)
        scratch.delete()
    except Scratch.DoesNotExist:
        return redirect_and_flash_error(
            request,
            "This scratch does not exist, please try again with a valid id.")
    return redirect_and_flash_success(request,
                                      "Scratch deleted successfully",
                                      path="/")


def view_scratches(request):
    # Get a list of (id,school_name) tuples
    c_scratches = [(s.team.pk, str(s), 0, "") for s in Scratch.objects.all()]
    return render(
        request, "common/list_data.html", {
            "item_type": "team",
            "title": "Viewing All Scratches for Teams",
            "item_list": c_scratches
        })

def get_settings_from_yaml():

    settings_dir = os.path.join(settings.BASE_DIR, "settings")

    all_settings = []
    setting_dict = {}
    categories = []

    for filename in sorted(os.listdir(settings_dir)):
        yaml_file = os.path.join(settings_dir, filename)

        with open(yaml_file, "r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream)

        category_info = data["category"]
        category_settings = data["settings"]

        category_id = category_info.get("id")

        categories.append(category_info)
        setting_dict[category_id] = []

        for setting in category_settings:
            all_settings.append(setting)
            setting_dict[category_id].append(setting["name"])

    if all_settings:
        stored_settings = {
            ts.key: ts.value if ts.value_string is None else ts.value_string
            for ts in TabSettings.objects.filter(
                key__in=(setting["name"] for setting in all_settings)
            )
        }
        for setting in all_settings:
            stored_value = stored_settings.get(setting["name"])
            if stored_value is None:
                continue
            if setting.get("type") == "boolean":
                setting["value"] = stored_value == 1
            else:
                setting["value"] = stored_value

    categories.sort(key=lambda x: x.get("order", 999))

    return all_settings, setting_dict, categories

### SETTINGS VIEWS ###


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def settings_form(request):
    yaml_settings, setting_dict, categories = get_settings_from_yaml()

    if request.method == "POST":
        form = SettingsForm(request.POST, settings=yaml_settings)

        if form.is_valid():
            form.save()
            invalidate_all_public_caches()
            return redirect_and_flash_success(
                request,
                "Tab settings updated!",
                path=reverse("settings_form")
            )
    else:
        form = SettingsForm(settings=yaml_settings)

    categories_with_fields = [
        {
            **category,
            "fields": [form[f"setting_{sname}"] for
                       sname in setting_dict[category["id"]]]
        }
        for category in categories
    ]

    return render(
        request, "tab/settings_form.html", {
            "form": form,
            "categories": categories_with_fields,
            "title": "Tab Settings"
        })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def public_home_shortcuts(request):
    if request.method == "POST":
        form = PublicHomeShortcutsForm(request.POST)
        if form.is_valid():
            form.save()
            invalidate_all_public_caches()
            return redirect_and_flash_success(
                request,
                "Homepage setup updated!",
                path=reverse("homepage_setup"),
            )
    else:
        form = PublicHomeShortcutsForm()

    return render(
        request,
        "tab/public_home_shortcuts_form.html",
        {
            "form": form,
            "title": "Homepage Setup",
        },
    )


def upload_data(request):
    team_info = {"errors": [], "uploaded": False}
    judge_info = {"errors": [], "uploaded": False}
    room_info = {"errors": [], "uploaded": False}
    scratch_info = {"errors": [], "uploaded": False}

    if request.method == "POST":
        form = UploadDataForm(request.POST, request.FILES)
        if form.is_valid():
            if "team_file" in request.FILES:
                team_info["errors"] = import_teams.import_teams(
                    request.FILES["team_file"])
                team_info["uploaded"] = True
            if "judge_file" in request.FILES:
                judge_info["errors"] = import_judges.import_judges(
                    request.FILES["judge_file"])
                judge_info["uploaded"] = True
            if "room_file" in request.FILES:
                room_info["errors"] = import_rooms.import_rooms(
                    request.FILES["room_file"])
                room_info["uploaded"] = True
            if "scratch_file" in request.FILES:
                scratch_info["errors"] = import_scratches.import_scratches(
                    request.FILES["scratch_file"])
                scratch_info["uploaded"] = True

        if not team_info["errors"] + judge_info["errors"] + \
                room_info["errors"] + scratch_info["errors"]:
            return redirect_and_flash_success(request,
                                              "Data imported successfully")
    else:
        form = UploadDataForm()
    return render(
        request, "common/data_upload.html", {
            "form": form,
            "title": "Upload Input Files",
            "team_info": team_info,
            "judge_info": judge_info,
            "room_info": room_info,
            "scratch_info": scratch_info
        })


def force_cache_refresh(request):
    key = request.GET.get("key", "")

    cache_logic.invalidate_cache(key)

    redirect_to = request.GET.get("next", "/")

    return redirect_and_flash_success(request,
                                      "Refreshed!",
                                      path=redirect_to)


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def generate_archive(request):
    tournament_name = request.META["SERVER_NAME"].split(".")[0]
    filename = tournament_name + ".xml"

    xml = ArchiveExporter(tournament_name).export_tournament()

    response = HttpResponse(xml, content_type="text/xml; charset=utf-8")
    response["Content-Length"] = len(xml)
    response["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@permission_required("tab.tab_settings.can_change", login_url="/403")
def simulate_round(request):
    enviornment = os.environ.get("MITTAB_ENV")
    if enviornment in ("development", "test-deployment"):
        call_command("simulate_rounds")
        return redirect_and_flash_success(request, "Simulated round")
    return redirect_and_flash_error(request, "Simulated rounds are disabled")

def room_tag(request, tag_id=None):
    tag = None
    if tag_id is not None:
        tag = RoomTag.objects.filter(pk=tag_id).first()

    if request.method == "POST":
        # _method is a hidden field used to simulate DELETE requests
        if request.POST.get("_method") == "DELETE":
            if tag is not None:
                tag.delete()
                return redirect_and_flash_success(request, "Tag deleted successfully")
            return redirect_and_flash_error(request, "Tag does not exist")

        form = RoomTagForm(request.POST, instance=tag)

        if not form.is_valid():
            return redirect_and_flash_error(request, "Error saving tag.")
        priority = form.cleaned_data.get("priority")
        if priority < 0 or priority > 100:
            return redirect_and_flash_error(request,
                                            "Priority must be between 0 and 100.")
        tag_instance = form.save()
        path = reverse("manage_room_tags")
        message = (
            f"Tag {tag_instance.tag} "
            f"{'updated' if tag else 'created'} successfully"
        )
        return redirect_and_flash_success(request, message,
                                          path=path)

    form = RoomTagForm(instance=tag)
    return render(request, "common/data_entry.html", {
        "form": form,
        "links": [],
        "tag_obj": tag,
        "title": f"Viewing Tag: {tag.tag}" if tag else "Create New Tag"
    })

def manage_room_tags(request):
    if request.method == "POST":
        return room_tag(request)
    form = MiniRoomTagForm(request.POST or None)
    room_tags = RoomTag.objects.all().order_by("-priority")
    return render(request, "pairing/manage_room_tags.html",
                  {"room_tags": room_tags,
                   "form": form})

def batch_checkin(request):
    round_numbers = list([i + 1 for i in range(TabSettings.get("tot_rounds"))])
    all_round_numbers = [0] + round_numbers

    team_data = [
        {"entity": t, "school": t.school, "debaters": t.debaters_display,
         "checked_in": t.checked_in}
        for t in Team.objects.prefetch_related("school", "debaters").all()
    ]

    judge_data = [
        {"entity": j, "schools": j.schools.all(),
         "checkins": [rn in {c.round_number for c in j.checkin_set.all()}
                      for rn in all_round_numbers]}
        for j in Judge.objects.prefetch_related("checkin_set", "schools")
    ]

    room_data = [
        {"entity": r,
         "checkins": [rn in {c.round_number for c in r.roomcheckin_set.all()}
                      for rn in all_round_numbers]}
        for r in Room.objects.prefetch_related("roomcheckin_set")
    ]

    def column_counts(rows):
        counts = [0] * len(all_round_numbers)
        for row in rows:
            for idx, checked in enumerate(row["checkins"]):
                if checked:
                    counts[idx] += 1
        return counts

    judge_counts = column_counts(judge_data)
    room_counts = column_counts(room_data)

    def round_count_data(counts):
        return [{
            "index": idx + 1,
            "round_number": round_numbers[idx],
            "count": counts[idx + 1]
        } for idx in range(len(round_numbers))]

    judge_round_counts = round_count_data(judge_counts)
    room_round_counts = round_count_data(room_counts)

    return render(request, "batch_check_in/check_in.html", {
        "team_data": team_data,
        "team_headers": ["School", "Team", "Debater Names"],
        "judge_data": judge_data,
        "judge_headers": ["School", "Judge"],
        "room_data": room_data,
        "room_headers": ["Room"],
        "round_numbers": round_numbers,
        "team_total_count": len(team_data),
        "team_checked_in_count": sum(1 for t in team_data if t["checked_in"]),
        "judge_total_count": len(judge_data),
        "room_total_count": len(room_data),
        "judge_round_counts": judge_round_counts,
        "room_round_counts": room_round_counts,
        "judge_outround_count": judge_counts[0] if judge_counts else 0,
        "room_outround_count": room_counts[0] if room_counts else 0,
    })


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def public_rankings_control(request):
    tot_rounds = int(TabSettings.get("tot_rounds", 0) or 0)

    if request.method == "POST":
        validation_errors = []
        standings_settings = get_all_standings_publication_settings()
        for standing in standings_settings:
            slug = standing["slug"]
            published = bool(request.POST.get(f"standings_{slug}"))
            set_standings_publication_setting(slug, published)

        ranking_settings = get_all_ranking_settings()
        for ranking in ranking_settings:
            slug = ranking["slug"]
            is_public = bool(request.POST.get(f"{slug}_public"))
            include_speaks = bool(request.POST.get(f"{slug}_include_speaks"))
            raw_max_visible = (request.POST.get(f"{slug}_max_visible") or "").strip()
            if raw_max_visible:
                try:
                    parsed_max_visible = int(raw_max_visible)
                except (TypeError, ValueError):
                    parsed_max_visible = ranking["max_visible"]
                    validation_errors.append(
                        f"Invalid max visible value for {ranking['label']}. "
                        "Keeping the previous value."
                    )
            else:
                parsed_max_visible = ranking["max_visible"]
            max_visible = max(1, parsed_max_visible)

            up_to_round = ranking.get("up_to_round") or 0
            raw_up_to_round = (request.POST.get(f"{slug}_up_to_round") or "").strip()
            if raw_up_to_round:
                try:
                    up_to_round = int(raw_up_to_round)
                except (TypeError, ValueError):
                    validation_errors.append(
                        f"Invalid 'up to round' value for {ranking['label']}. "
                        "Keeping the previous value."
                    )

            set_ranking_settings(
                slug,
                public=is_public,
                include_speaks=include_speaks,
                max_visible=max_visible,
                up_to_round=up_to_round,
            )

        for round_number in range(1, tot_rounds + 1):
            set_ballot_round_settings(
                round_number,
                visible=bool(request.POST.get(f"round_{round_number}_visible")),
                include_speaks=bool(
                    request.POST.get(f"round_{round_number}_include_speaks")
                ),
                include_ranks=bool(
                    request.POST.get(f"round_{round_number}_include_ranks")
                ),
            )

        invalidate_public_rankings_cache()
        for message_text in validation_errors:
            messages.warning(request, message_text)
        messages.success(request, "Public rankings settings saved.")
        return redirect("public_rankings_control")

    standings_settings = get_all_standings_publication_settings()
    ranking_settings = get_all_ranking_settings()
    ballot_settings = get_all_ballot_round_settings(tot_rounds)
    paired_rounds = get_paired_round_numbers()

    return render(
        request,
        "rankings/public_rankings_control.html",
        {
            "standings_settings": standings_settings,
            "ranking_settings": ranking_settings,
            "ballot_settings": ballot_settings,
            "tot_rounds": tot_rounds,
            "paired_rounds": paired_rounds,
        },
    )


def forum_post(request):
    # Get dino judges
    dinos = Judge.objects.filter(is_dino=True).values_list("name", flat=True)

    # Get top debaters (limiting to top 10)
    varsity_debaters, nov_debaters = get_speaker_rankings(None)
    nov_debaters = nov_debaters[:min(10, len(nov_debaters))]
    varsity_debaters = varsity_debaters[:min(10, len(varsity_debaters))]

    # Get qualifying teams and debaters
    qualifying_teams = Team.objects.prefetch_related("debaters").annotate(
        num_rounds=models.Count("gov_team", distinct=True) +
        models.Count("opp_team", distinct=True)
    ).filter(
        num_rounds__gte=3
    )

    qualifying_novices = Debater.objects.filter(
        team__in=qualifying_teams,
        novice_status=True
    )

    team_count = qualifying_teams.count()
    novice_count = qualifying_novices.count()

    # Get team rankings and calculate breaking teams
    varsity_teams, nov_teams = get_team_rankings(None)
    nov_teams_to_break = TabSettings.get("nov_teams_to_break")
    var_teams_to_break = TabSettings.get("var_teams_to_break")

    varsity_teams = varsity_teams[:var_teams_to_break]

    # Calculate novice teams that made varsity break
    novice_teams_in_varsity_break = sum(
        1 for team in nov_teams if team in varsity_teams
    )
    novice_teams = nov_teams[:nov_teams_to_break + novice_teams_in_varsity_break]

    # Get outround data
    varsity_outs = create_forum_view_data(0)
    novice_outs = create_forum_view_data(1)

    # Determine champions
    novice_champ = None
    varsity_champ = None

    finals = Outround.objects.filter(num_teams=2)
    varsity_finals = finals.filter(type_of_round=0).first()
    novice_finals = finals.filter(type_of_round=1).first()

    if varsity_finals and varsity_finals.victor:
        varsity_champ = (varsity_finals.gov_team if varsity_finals.victor % 2 == 1
                         else varsity_finals.opp_team)

    if novice_finals and novice_finals.victor:
        novice_champ = (novice_finals.gov_team if novice_finals.victor % 2 == 1
                        else novice_finals.opp_team)

    return render(request, "tab/forum_post.html", {
        "dinos": dinos,
        "nov_debaters": nov_debaters,
        "varsity_debaters": varsity_debaters,
        "team_count": team_count,
        "novice_count": novice_count,
        "varsity_teams": varsity_teams,
        "novice_teams": novice_teams,
        "novice_outs": novice_outs["results"],
        "varsity_outs": varsity_outs["results"],
        "novice_champ": novice_champ,
        "varsity_champ": varsity_champ,
    })
