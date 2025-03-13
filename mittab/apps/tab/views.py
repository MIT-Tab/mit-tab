import os
from django.contrib.auth.decorators import permission_required
from django.contrib.auth import logout
from django.conf import settings
from django.http import HttpResponse, JsonResponse, Http404
from django.shortcuts import render, reverse, get_object_or_404
from django.core.management import call_command
import yaml

from mittab.apps.tab.archive import ArchiveExporter
from mittab.apps.tab.forms import RoomTagForm, SchoolForm, RoomForm, \
    UploadDataForm, ScratchForm, SettingsForm
from mittab.apps.tab.helpers import redirect_and_flash_error, \
    redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs import cache_logic
from mittab.libs.tab_logic import TabFlags
from mittab.libs.data_import import import_judges, import_rooms, import_teams, \
    import_scratches


def index(request):
    school_list = [(school.pk, school.name) for school in School.objects.all()]
    judge_list = [(judge.pk, judge.name) for judge in Judge.objects.all()]
    team_list = [(team.pk, team.display_backend) for team in Team.objects.all()]
    debater_list = [(debater.pk, debater.display)
                    for debater in Debater.objects.all()]
    room_list = [(room.pk, room.name) for room in Room.objects.all()]

    number_teams = len(team_list)
    number_judges = len(judge_list)
    number_schools = len(school_list)
    number_debaters = len(debater_list)
    number_rooms = len(room_list)

    return render(request, "common/index.html", locals())


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
            form.save()
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
            return redirect_and_flash_success(
                request, "School {} updated successfully".format(
                    form.cleaned_data["name"]))
    else:
        form = SchoolForm(instance=school)
        links = [("/school/" + str(school_id) + "/delete/", "Delete")]
        return render(
            request, "common/data_entry.html", {
                "form": form,
                "links": links,
                "title": "Viewing School: %s" % (school.name)
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
            return redirect_and_flash_success(
                request,
                "School {} created successfully".format(
                    form.cleaned_data["name"]),
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
            return redirect_and_flash_success(
                request, "School {} updated successfully".format(
                    form.cleaned_data["name"]))
    else:
        form = RoomForm(instance=room)
    return render(request, "common/data_entry.html", {
        "form": form,
        "links": [],
        "title": "Viewing Room: %s" % (room.name)
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
            return redirect_and_flash_success(
                request,
                "Room {} created successfully".format(
                    form.cleaned_data["name"]),
                path="/")
    else:
        form = RoomForm()
    return render(request, "common/data_entry.html", {
        "form": form,
        "title": "Create Room"
    })



@permission_required("tab.tab_settings.can_change", login_url="/403")
def room_check_in(request, room_id, round_number):
    room_id, round_number = int(room_id), int(round_number)

    if round_number < 0 or round_number > TabSettings.get("tot_rounds"):
        # 0 is so that outrounds don't throw an error
        raise Http404("Round does not exist")

    room = get_object_or_404(Room, pk=room_id)
    if request.method == "POST":
        if not room.is_checked_in_for_round(round_number):
            check_in = RoomCheckIn(room=room, round_number=round_number)
            check_in.save()
    elif request.method == "DELETE":
        if room.is_checked_in_for_round(round_number):
            check_ins = RoomCheckIn.objects.filter(room=room,
                                                   round_number=round_number)
            check_ins.delete()
    else:
        raise Http404("Must be POST or DELETE")
    return JsonResponse({"success": True})


@permission_required("tab.scratch.can_delete", login_url="/403/")
def delete_scratch(request, item_id, scratch_id):
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
    default_settings = []
    with open(settings.SETTING_YAML_PATH, "r") as stream:
        default_settings = yaml.safe_load(stream)

    to_return = []

    for setting in default_settings:
        tab_setting = TabSettings.objects.filter(key=setting["name"]).first()

        if tab_setting:
            if "type" in setting and setting["type"] == "boolean":
                setting["value"] = tab_setting.value == 1
            else:
                setting["value"] = tab_setting.value

        to_return.append(setting)

    return to_return

### SETTINGS VIEWS ###


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def settings_form(request):
    yaml_settings = get_settings_from_yaml()
    if request.method == "POST":
        _settings_form = SettingsForm(request.POST, settings=yaml_settings)

        if _settings_form.is_valid():
            _settings_form.save()
            return redirect_and_flash_success(
                request,
                "Tab settings updated!",
                path=reverse("settings_form")
            )
        return render(  # Allows for proper validation checking
            request, "tab/settings_form.html", {
                "form": settings_form,
            })

    _settings_form = SettingsForm(settings=yaml_settings)

    return render(
        request, "tab/settings_form.html", {
            "form": _settings_form,
        })


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
    response["Content-Disposition"] = "attachment; filename=%s" % filename
    return response

@permission_required("tab.tab_settings.can_change", login_url="/403")
def simulate_round(request):
    enviornment = os.environ.get("MITTAB_ENV")
    if enviornment in ("development", "test-deployment"):
        call_command("simulate_rounds")
        return redirect_and_flash_success(request, "Simulated round")
    return redirect_and_flash_error(request, "Simulated rounds are disabled")

def room_tag(request, tag_id=None):
    tag = get_object_or_404(RoomTag, pk=tag_id) if tag_id else None

    if request.method == "POST":
        form = RoomTagForm(request.POST, instance=tag)

        if not form.is_valid():
            return redirect_and_flash_error(request, "Error saving tag.")
        if form.cleaned_data.get("delete"):
            form.save()
            path = "/"
            message = "Deleted successfully"
        else:
            priority = form.cleaned_data.get("priority")
            if priority < 0 or priority > 100:
                return redirect_and_flash_error(request,
                                                "Priority must be between 0 and 100.")
            tag_instance = form.save()
            path = reverse("room_tag", args=[tag_instance.pk])
            message = f"Tag {tag_instance.tag} \
                {'updated' if tag else 'created'} successfully"
        return redirect_and_flash_success(request, message,
                                          path=path)

    form = RoomTagForm(instance=tag)
    return render(request, "common/data_entry.html", {
        "form": form,
        "links": [],
        "tag_obj": tag,
        "title": f"Viewing Tag: {tag.tag}" if tag else "Create New Tag"
    })

def batch_checkin(request):
    judges_and_checkins = []
    rooms_and_checkins = []


    teams = Team.objects.prefetch_related("school", "debaters").all()
    team_and_checkins = [(team.school.name,
                          team,
                          team.debaters.all(),
                          team.checked_in)
                         for team in teams]



    round_numbers = list([i + 1 for i in range(TabSettings.get("tot_rounds"))])
    all_round_numbers = [0]+round_numbers
    judges = Judge.objects.prefetch_related("checkin_set", "schools")

    for judge in judges:
        checkins = {checkin.round_number for checkin in judge.checkin_set.all()}
        checkins_list = [round_number in checkins for round_number in all_round_numbers]
        judges_and_checkins.append((judge.schools.all(), judge, checkins_list))



    rooms = Room.objects.prefetch_related("roomcheckin_set")

    for room in rooms:
        checkins = []
        checkins = {checkin.round_number for checkin in room.roomcheckin_set.all()}
        checkins_list = [round_number in checkins for round_number in all_round_numbers]
        rooms_and_checkins.append((room, checkins_list))

    return render(request, "batch_check_in/check_in.html", {
        "teams_and_checkins": team_and_checkins,
        "judges_and_checkins": judges_and_checkins,
        "round_numbers": round_numbers,
        "rooms_and_checkins": rooms_and_checkins,
        })
