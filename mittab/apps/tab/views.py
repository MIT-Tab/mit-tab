from django.shortcuts import render, redirect
from django.template import RequestContext, loader
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseForbidden
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.views import login
from django.contrib.auth import logout
from django.contrib import messages
from mittab.apps.tab.forms import SchoolForm, RoomForm, UploadDataForm, ScratchForm
from mittab.apps.tab.helpers import redirect_and_flash_error, \
        redirect_and_flash_success
from django.db import models
from mittab.apps.tab.models import *
from mittab.libs.tab_logic import TabFlags
from mittab.libs.data_import import import_judges, import_rooms, import_teams, \
        import_scratches

def index(request):
    number_teams = Team.objects.count()
    number_judges = Judge.objects.count()
    number_schools = School.objects.count()
    number_debaters = Debater.objects.count()
    number_rooms = Room.objects.count()

    school_list = [(school.pk,school.name) for school in School.objects.order_by('name')]
    judge_list = [(judge.pk,judge.name) for judge in Judge.objects.order_by('name')]
    team_list = [(team.pk,team.name) for team in Team.objects.order_by('name')]
    debater_list = [(debater.pk,debater.name) for debater in Debater.objects.order_by('name')]
    room_list = [(room.pk, room.name) for room in Room.objects.order_by('name')]

    return render(request, 'common/index.html',locals())

def tab_login(request):
    return login(request)

def tab_logout(request, *args):
    logout(request)
    return redirect_and_flash_success(request,
            "Successfully logged out",
            path="/")

def render_403(request, *args, **kwargs):
    response = render(request, 'common/403.html')
    response.status_code = 403
    return response

def render_404(request, *args, **kwargs):
    response = render(request, 'common/404.html')
    response.status_code = 404
    return response

def render_500(request, *args, **kwargs):
    response = render(request, 'common/500.html')
    response.status_code = 500
    return response

#View for manually adding scratches
def add_scratch(request):
    if request.method == 'POST':
        form = ScratchForm(request.POST)
        if (form.is_valid()):
          form.save()
        judge = form.cleaned_data['judge'].name
        team = form.cleaned_data['team'].name
        return redirect_and_flash_success(request,
                "Scratch created successfully")
    else:
        form = ScratchForm(initial={'scratch_type':0})
    return render(request, 'common/data_entry.html',
                              {'title':"Adding Scratch", 'form': form})


#### BEGIN SCHOOL ###
#Three views for entering, viewing, and editing schools
def view_schools(request):
    #Get a list of (id,school_name) tuples
    c_schools = [(s.pk,s.name,0,"") for s in School.objects.all().order_by("name")]
    return render(request, 'common/list_data.html',
            {'item_type':'school', 'title': "Viewing All Schools", 'item_list':c_schools})


def view_school(request, school_id):
    school_id = int(school_id)
    try:
        school = School.objects.get(pk=school_id)
    except School.DoesNotExist:
        return redirect_and_flash_error(request, "School not found")
    if request.method == 'POST':
        form = SchoolForm(request.POST,instance=school)
        if form.is_valid():
            try:
               form.save()
            except ValueError:
                return redirect_and_flash_error(request,
                        "School name cannot be validated, most likely a non-existent school")
            return redirect_and_flash_success(request,
                    "School {} updated successfully".format(form.cleaned_data['name']))
    else:
        form = SchoolForm(instance=school)
        links = [('/school/'+str(school_id)+'/delete/', 'Delete')]
        return render(request, 'common/data_entry.html',
                                 {'form': form,
                                  'links': links,
                                  'title': "Viewing School: %s" %(school.name)})

def enter_school(request):
    if request.method == 'POST':
        form = SchoolForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(request,
                        "School name cannot be validated, most likely a duplicate school")
            return redirect_and_flash_success(request,
                    "School {} created successfully".format(form.cleaned_data['name']),
                    path="/")
    else:
        form = SchoolForm()
    return render(request, 'common/data_entry.html', 
                              {'form': form, 'title': "Create School"})

@permission_required('tab.school.can_delete', login_url="/403/")
def delete_school(request, school_id):
    error_msg = None
    try :
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
    all_rooms = [(room.pk, room.name, flags(room), TabFlags.flags_to_symbols(flags(room))) 
                  for room in Room.objects.all().order_by("name")]
    filters, symbol_text = TabFlags.get_filters_and_symbols(all_flags)
    return render(request, 'common/list_data.html', 
                             {'item_type':'room',
                              'title': "Viewing All Rooms",
                              'item_list':all_rooms,
                              'symbol_text':symbol_text,
                              "filters": filters})

def view_room(request, room_id):
    room_id = int(room_id)
    try:
        room = Room.objects.get(pk=room_id)
    except Room.DoesNotExist:
        return redirect_and_flash_error(request, "Room not found")
    if request.method == 'POST':
        form = RoomForm(request.POST,instance=room)
        if form.is_valid():
            try:
               form.save()
            except ValueError:
                return redirect_and_flash_error(request,
                        "Room name cannot be validated, most likely a non-existent room")
            return redirect_and_flash_success(request,
                    "School {} updated successfully".format(form.cleaned_data['name']))
    else:
        form = RoomForm(instance=room)
    return render(request, 'common/data_entry.html',
                                {'form': form, 'links': [],
                                'title': "Viewing Room: %s"%(room.name)})

def enter_room(request):
    if request.method == 'POST':
        form = RoomForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(request,
                        "Room name cannot be validated, most likely a duplicate room")
            return redirect_and_flash_success(request,
                    "Room {} created successfully".format(form.cleaned_data['name']),
                    path="/")
    else:
        form = RoomForm()
    return render(request, 'common/data_entry.html',
                             {'form': form, 'title': 'Create Room'})

@permission_required('tab.scratch.can_delete', login_url="/403/")
def delete_scratch(request, item_id, scratch_id):
    try:
        scratch_id = int(scratch_id)
        scratch = Scratch.objects.get(pk=scratch_id)
        scratch.delete()
    except Scratch.DoesNotExist:
        return redirect_and_flash_error(request,
                "This scratch does not exist, please try again with a valid id.")
    return redirect_and_flash_success(request,
            "Scratch deleted successfully",
            path="/")

def view_scratches(request):
    # Get a list of (id,school_name) tuples
    c_scratches = [(s.team.pk, str(s), 0, "") for s in Scratch.objects.all()]
    return render(request, 'common/list_data.html', 
                             {'item_type':'team',
                              'title': "Viewing All Scratches for Teams",
                              'item_list':c_scratches})

def upload_data(request):
    team_info = { 'errors': [], 'uploaded': False }
    judge_info = { 'errors': [], 'uploaded': False }
    room_info = { 'errors': [], 'uploaded': False }
    scratch_info = { 'errors': [], 'uploaded': False }

    if request.method == 'POST':
        form = UploadDataForm(request.POST, request.FILES)
        if form.is_valid():
            if 'team_file' in request.FILES:
                team_info['errors'] = import_teams.import_teams(request.FILES['team_file'])
                team_info['uploaded'] = True
            if 'judge_file' in request.FILES:
                judge_info['errors'] = import_judges.import_judges(request.FILES['judge_file'])
                judge_info['uploaded'] = True
            if 'room_file' in request.FILES:
                room_info['errors'] = import_rooms.import_rooms(request.FILES['room_file'])
                room_info['uploaded'] = True
            if 'scratch_file' in request.FILES:
                scratch_info['errors'] = import_scratches.import_scratches(request.FILES['scratch_files'])
                scratch_info['uploaded'] = True

        if not team_info['errors'] + judge_info['errors'] + \
                room_info['errors'] + scratch_info['errors']:
            return redirect_and_flash_success(request, "Data imported successfully")
    else:
        form = UploadDataForm()
    return render(request, 'common/data_upload.html',
            {'form': form, 'title': 'Upload Input Files',
                'team_info': team_info, 'judge_info': judge_info,
                'room_info': room_info, 'scratch_info': scratch_info})
