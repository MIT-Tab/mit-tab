from django.shortcuts import render
from django.template import RequestContext, loader
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseForbidden
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.views import login
from mittab.apps.tab.forms import SchoolForm, RoomForm, UploadDataForm, ScratchForm
from django.db import models
from mittab.apps.tab.models import *
from mittab.libs.tab_logic import TabFlags
from mittab.libs.data_import import import_judges, import_rooms, import_teams

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

    return render(request, 'index.html',locals())

def tab_login(request):
    return login(request, extra_context={'no_navigation': True})

def render_403(request):
    t = loader.get_template('403.html')
    c = RequestContext(request, {})
    return HttpResponseForbidden(t.render(c))

#View for manually adding scratches
def add_scratch(request):
    if request.method == 'POST':
        form = ScratchForm(request.POST)
        if (form.is_valid()):
          form.save()
        judge = form.cleaned_data['judge'].name
        team = form.cleaned_data['team'].name
        return render(request, 'thanks.html', 
                                  {'data_type': "Scratch",
                                  'data_name': ' from {0} on {1}'.format(team, judge),
                                  'data_modification': "CREATED",
                                  'enter_again': True})
    else:
        form = ScratchForm(initial={'scratch_type':0})
    return render(request, 'data_entry.html', 
                              {'title':"Adding Scratch",
                              'form': form})


#### BEGIN SCHOOL ###
#Three views for entering, viewing, and editing schools
def view_schools(request):
    #Get a list of (id,school_name) tuples
    c_schools = [(s.pk,s.name,0,"") for s in School.objects.all().order_by("name")]
    return render(request, 'list_data.html',
            {'item_type':'school', 'title': "Viewing All Schools", 'item_list':c_schools})


def view_school(request, school_id):
    school_id = int(school_id)
    try:
        school = School.objects.get(pk=school_id)
    except School.DoesNotExist:
        return render(request, 'error.html', 
                                 {'error_type': "View School",
                                  'error_name': str(school_id),
                                  'error_info':"No such school"})
    if request.method == 'POST':
        form = SchoolForm(request.POST,instance=school)
        if form.is_valid():
            try:
               form.save()
            except ValueError:
                return render(request, 'error.html', 
                                         {'error_type': "School",
                                          'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"School name cannot be validated, most likely a non-existent school"})
            return render(request, 'thanks.html', 
                                     {'data_type': "School",
                                      'data_name': "["+form.cleaned_data['name']+"]"})
    else:
        form = SchoolForm(instance=school)
        links = [('/school/'+str(school_id)+'/delete/', 'Delete', True)]
        return render(request, 'data_entry.html', 
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
                return render(request, 'error.html', 
                                         {'error_type': "School",'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"School name cannot be validated, most likely a duplicate school"})
            return render(request, 'thanks.html',
                                     {'data_type': "School",
                                      'data_name': "["+form.cleaned_data['name']+"]",
                                      'data_modification': "CREATED",
                                      'enter_again': True})
    else:
        form = SchoolForm()
    return render(request, 'data_entry.html', 
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
        return render(request, 'error.html', 
                                 {'error_type': "Delete School",
                                  'error_name': "School with id %s" % (school_id),
                                  'error_info': error_msg})
    return render(request, 'thanks.html', 
                             {'data_type': "School",
                              'data_name': "["+str(school_id)+"]",
                              'data_modification': 'DELETED'})

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
  
    return render(request, 'list_data.html', 
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
        return render(request, 'error.html', 
                                 {'error_type': "View Room",
                                  'error_name': str(room_id),
                                  'error_info':"No such room"})
    if request.method == 'POST':
        form = RoomForm(request.POST,instance=room)
        if form.is_valid():
            try:
               form.save()
            except ValueError:
                return render(request, 'error.html', 
                                         {'error_type': "Room",
                                          'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"Room name cannot be validated, most likely a non-existent room"})
            return render(request, 'thanks.html', 
                                     {'data_type': "Room",
                                      'data_name': "["+form.cleaned_data['name']+"]"})
    else:
        form = RoomForm(instance=room)
        return render(request, 'data_entry.html', 
                                 {'form': form,
                                  'links': [],
                                  'title': "Viewing Room: %s"%(room.name)})

def enter_room(request):
    if request.method == 'POST':
        form = RoomForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return render(request, 'error.html', 
                                         {'error_type': "Room",'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"Room name cannot be validated, most likely a duplicate room"})
            return render(request, 'thanks.html', 
                                     {'data_type': "Room",
                                      'data_name': "["+form.cleaned_data['name']+"]",
                                      'data_modification': "CREATED",
                                      'enter_again': True})
    else:
        form = RoomForm()
    return render(request, 'data_entry.html',
                             {'form': form, 'title': 'Create Room'})

@permission_required('tab.room.can_delete', login_url="/403/")
def delete_room(request, room_id):
    school_id = int(room_id)
    try :
        r = Room.objects.get(pk=room_id)
        r.delete()
    except Room.DoesNotExist:
        return render(request, 'error.html',
                                 {'error_type': "Delete Room",
                                  'error_name': str(room_id),
                                  'error_info':"This room does not exist, please try again with a valid id. "})
    return render(request, 'thanks.html',
                             {'data_type': "Room",
                              'data_name': "["+str(room_id)+"]",
                              'data_modification': 'DELETED'})

@permission_required('tab.scratch.can_delete', login_url="/403/")
def delete_scratch(request, item_id, scratch_id):
    try:
        scratch_id = int(scratch_id)
        scratch = Scratch.objects.get(pk=scratch_id)
        scratch.delete()
    except Scratch.DoesNotExist:
        return render(request, 'error.html', 
                                 {'error_type': "Delete Scratch",
                                  'error_name': str(scratch_id),
                                  'error_info':"This scratch does not exist, please try again with a valid id. "})
    return render(request, 'thanks.html', 
                             {'data_type': "Scratch",
                              'data_name': "["+str(scratch_id)+"]",
                              'data_modification': 'DELETED'})

def view_scratches(request):
    # Get a list of (id,school_name) tuples
    c_scratches = [(s.team.pk, str(s)) for s in Scratch.objects.all()]
    return render(request, 'list_data.html', 
                             {'item_type':'team',
                              'title': "Viewing All Scratches for Teams",
                              'item_list':c_scratches})

def upload_data(request):
    if request.method == 'POST':
      form = UploadDataForm(request.POST, request.FILES)
      if form.is_valid():
        judge_errors = room_errors = team_errors = []
        importName = ''
        results = ''

        if 'team_file' in request.FILES:
          team_errors = import_teams.import_teams(request.FILES['team_file'])
          importName += request.FILES['team_file'].name + ' '
          if len(team_errors) > 0:
            results += 'Team Import Errors (Please Check These Manually):\n'
            for e in team_errors:
              results += '            ' + e + '\n'
        if 'judge_file' in request.FILES:
          judge_errors = import_judges.import_judges(request.FILES['judge_file'])
          importName += request.FILES['judge_file'].name + ' '
          if len(judge_errors) > 0:
            results += 'Judge Import Errors (Please Check These Manually):\n'
            for e in judge_errors:
              results += '            ' + e + '\n'
        if 'room_file' in request.FILES:
          room_errors = import_rooms.import_rooms(request.FILES['room_file'])
          importName += request.FILES['room_file'].name + ' '
          if len(room_errors) > 0:
            results += 'Room Import Errors (Please Check These Manually):\n'
            for e in room_errors:
              results += '            ' + e + '\n'

        return render(request, 'thanks.html', 
                                 {'data_type': "Database data",
                                  'data_name': importName,
                                  'data_modification': "INPUT",
                                  'results': True,
                                  'data_results': results})
    else:
      form = UploadDataForm()
    return render(request, 'data_entry.html', 
                              {'form': form,
                               'title': 'Upload Input Files'})
    
