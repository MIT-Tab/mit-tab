from django.shortcuts import render_to_response
from django.template import RequestContext, loader
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseForbidden
from django.contrib.auth.decorators import permission_required
from forms import SchoolForm, RoomForm
from django.db import models
from models import *

def index(request):
    number_teams = Team.objects.count()
    number_judges = Judge.objects.count()
    number_schools = School.objects.count()
    school_list = [(school.pk,school.name) for school in School.objects.order_by('name')]
    judge_list = [(judge.pk,judge.name) for judge in Judge.objects.order_by('name')]
    team_list = [(team.pk,team.name) for team in Team.objects.order_by('name')]
    debater_list = [(debater.pk,debater.name) for debater in Debater.objects.order_by('name')]
    return render_to_response('index.html',locals(),context_instance=RequestContext(request))

def render_403(request):
    t = loader.get_template('403.html')
    c = RequestContext(request, {})
    return HttpResponseForbidden(t.render(c))
    
#### BEGIN SCHOOL ###
#Three views for entering, viewing, and editing schools
def view_schools(request):
    #Get a list of (id,school_name) tuples
    c_schools = [(s.pk,s.name) for s in School.objects.all().order_by("name")]
    return render_to_response('list_data.html', 
                             {'item_type':'school',
                              'title': "Viewing All Schools",
                              'item_list':c_schools}, context_instance=RequestContext(request))


def view_school(request, school_id):
    school_id = int(school_id)
    try:
        school = School.objects.get(pk=school_id)
    except School.DoesNotExist:
        return render_to_response('error.html', 
                                 {'error_type': "View School",
                                  'error_name': str(school_id),
                                  'error_info':"No such school"}, 
                                  context_instance=RequestContext(request))
    if request.method == 'POST':
        form = SchoolForm(request.POST,instance=school)
        if form.is_valid():
            try:
               form.save()
            except ValueError:
                return render_to_response('error.html', 
                                         {'error_type': "School",
                                          'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"School name cannot be validated, most likely a non-existent school"}, 
                                          context_instance=RequestContext(request))
            return render_to_response('thanks.html', 
                                     {'data_type': "School",
                                      'data_name': "["+form.cleaned_data['name']+"]"}, 
                                      context_instance=RequestContext(request))
    else:
        form = SchoolForm(instance=school)
        links = [('/school/'+str(school_id)+'/delete/', 'Delete', True)]
        return render_to_response('data_entry.html', 
                                 {'form': form,
                                  'links': links,
                                  'title': "Viewing School: %s" %(school.name)},
                                  context_instance=RequestContext(request))
    #return  render_to_response('display_info.html', {'id':school_id} ,context_instance=RequestContext(request))
                                
def enter_school(request):
    if request.method == 'POST':
        form = SchoolForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return render_to_response('error.html', 
                                         {'error_type': "School",'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"School name cannot be validated, most likely a duplicate school"}, 
                                          context_instance=RequestContext(request))
            return render_to_response('thanks.html', 
                                     {'data_type': "School",
                                      'data_name': "["+form.cleaned_data['name']+"]",
                                      'data_modification': "CREATED" }, 
                                      context_instance=RequestContext(request))
    else:
        form = SchoolForm()
    return render_to_response('data_entry.html', {'form': form}, context_instance=RequestContext(request))

@permission_required('tab.school.can_delete', login_url="/403/")    
def delete_school(request, school_id):
    error_msg = None
    try :
        school_id = int(school_id)
        school = School.objects.get(pk=school_id)
        school.delete()
    except School.DoesNotExist:
        error_msg = "That school does not exist"
    except Exception, e:
        error_msg = str(e)
    if error_msg:
        return render_to_response('error.html', 
                                 {'error_type': "Delete School",
                                  'error_name': "School with id %s" % (school_id),
                                  'error_info': error_msg}, 
                                  context_instance=RequestContext(request))
    return render_to_response('thanks.html', 
                             {'data_type': "School",
                              'data_name': "["+str(school_id)+"]",
                              'data_modification': 'DELETED'}, 
                              context_instance=RequestContext(request)) 

#### END SCHOOL ###

#### BEGIN ROOM ###
def view_rooms(request):
    #Get a list of (id,school_name) tuples
    all_rooms = [(room.pk,room.name) for room in Room.objects.all().order_by("name")]
    return render_to_response('list_data.html', 
                             {'item_type':'room',
                              'title': "Viewing All Rooms",
                              'item_list':all_rooms}, context_instance=RequestContext(request))

def view_room(request, room_id):
    room_id = int(room_id)
    try:
        room = Room.objects.get(pk=room_id)
    except Room.DoesNotExist:
        return render_to_response('error.html', 
                                 {'error_type': "View Room",
                                  'error_name': str(room_id),
                                  'error_info':"No such room"}, 
                                  context_instance=RequestContext(request))
    if request.method == 'POST':
        form = RoomForm(request.POST,instance=room)
        if form.is_valid():
            try:
               form.save()
            except ValueError:
                return render_to_response('error.html', 
                                         {'error_type': "Room",
                                          'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"Room name cannot be validated, most likely a non-existent room"}, 
                                          context_instance=RequestContext(request))
            return render_to_response('thanks.html', 
                                     {'data_type': "Room",
                                      'data_name': "["+form.cleaned_data['name']+"]"}, 
                                      context_instance=RequestContext(request))
    else:
        form = RoomForm(instance=room)
        links = [('/room/'+str(room_id)+'/delete/', 'Delete', True)]
        return render_to_response('data_entry.html', 
                                 {'form': form,
                                  'links': links,
                                  'title': "Viewing Room: %s"%(room.name)}, 
                                 context_instance=RequestContext(request))
    #return  render_to_response('display_info.html', {'id':school_id} ,context_instance=RequestContext(request))

def enter_room(request):
    if request.method == 'POST':
        form = RoomForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return render_to_response('error.html', 
                                         {'error_type': "Room",'error_name': "["+form.cleaned_data['name']+"]",
                                          'error_info':"Room name cannot be validated, most likely a duplicate room"}, 
                                          context_instance=RequestContext(request))
            return render_to_response('thanks.html', 
                                     {'data_type': "Room",
                                      'data_name': "["+form.cleaned_data['name']+"]",
                                      'data_modification': "CREATED" }, 
                                      context_instance=RequestContext(request))
    else:
        form = RoomForm()
    return render_to_response('data_entry.html', {'form': form}, context_instance=RequestContext(request))    
    
@permission_required('tab.room.can_delete', login_url="/403/")    
def delete_room(request, room_id):
    school_id = int(room_id)
    try :
        r = Room.objects.get(pk=room_id)
        r.delete()
    except Room.DoesNotExist:
        return render_to_response('error.html', 
                                 {'error_type': "Delete Room",
                                  'error_name': str(room_id),
                                  'error_info':"This room does not exist, please try again with a valid id. "}, 
                                  context_instance=RequestContext(request))
    return render_to_response('thanks.html', 
                             {'data_type': "Room",
                              'data_name': "["+str(room_id)+"]",
                              'data_modification': 'DELETED'}, 
                              context_instance=RequestContext(request))

@permission_required('tab.scratch.can_delete', login_url="/403/")                                  
def delete_scratch(request, item_id, scratch_id):
    try:
        scratch_id = int(scratch_id)
        scratch = Scratch.objects.get(pk=scratch_id)
        scratch.delete()
    except Scratch.DoesNotExist:
        return render_to_response('error.html', 
                                 {'error_type': "Delete Scratch",
                                  'error_name': str(scratch_id),
                                  'error_info':"This scratch does not exist, please try again with a valid id. "}, 
                                  context_instance=RequestContext(request))
    return render_to_response('thanks.html', 
                             {'data_type': "Scratch",
                              'data_name': "["+str(scratch_id)+"]",
                              'data_modification': 'DELETED'}, 
                              context_instance=RequestContext(request))
    
