from django.contrib.auth.decorators import permission_required
from django.contrib.auth.views import login
from django.http import HttpResponseForbidden
from django.shortcuts import render_to_response
from django.template import RequestContext, loader

from forms import SchoolForm, RoomForm, UploadDataForm, ScratchForm
from mittab.libs.data_import import import_judges, import_rooms, import_teams
from mittab.libs.tab_logic import TabFlags
from models import *


def index(request):
    """
    Handles the display of the index page for MITTAB, i.e. that page with all the schools, judges, teams, debaters,
    rooms, etc when you log into the program.
    """
    number_teams = Team.objects.count()
    number_judges = Judge.objects.count()
    number_schools = School.objects.count()
    number_debaters = Debater.objects.count()
    number_rooms = Room.objects.count()

    # TODO Perhaps change the order in which these are sorted? Order judges in rank, etc?
    school_list = [(school.pk, school.name) for school in School.objects.order_by('name')]
    judge_list = [(judge.pk, judge.name) for judge in Judge.objects.order_by('name')]
    team_list = [(team.pk, team.name) for team in Team.objects.order_by('name')]
    debater_list = [(debater.pk, debater.name) for debater in Debater.objects.order_by('name')]
    room_list = [(room.pk, room.name) for room in Room.objects.order_by('name')]

    return render_to_response('index.html', locals(), context_instance=RequestContext(request))


def tab_login(request):
    """
    Returns a django.contrib.auth.views.login query
    """
    return login(request, extra_context={'no_navigation': True})


def render_403(request):
    """
    Renders a forbidden page.
    """
    t = loader.get_template('403.html')
    c = RequestContext(request, {})
    return HttpResponseForbidden(t.render(c))


'''
School view section.
Includes views for viewing schools, viewing one school, entering a school, and deleting a school.
'''


def view_schools(request):
    """
    Gets a list of (id, school_name) tuples. This is returned when you click the button to view the schols list from
    the front page. I am unsure of its utility given that this is already done on the front page.
    """
    c_schools = [(s.pk, s.name) for s in School.objects.all().order_by("name")]
    return render_to_response('list_data.html',
                              {'item_type': 'school',
                               'title': "Viewing All Schools",
                               'item_list': c_schools}, context_instance=RequestContext(request))


def view_school(request, school_id):
    """
    Allows for you to view the school. First, it checks whether that school exists or not. Then, it checks whether the
    render is a POST or GET request. If you are viewing the page, go to the _else_ section of the code.
    :param school_id: is the ID of the school in the program. It is a numerical value and _not_ simply the same as the
        school name. You can view the school_id by clicking on the school and checking the URL. It is _not_ the same as
        the number accompanying the school on the index page.
    """
    school_id = int(school_id)

    # Check existence, if not, render an error page
    try:
        school = School.objects.get(pk=school_id)
    except School.DoesNotExist:
        return render_to_response('error.html',
                                  {'error_type': "View School",
                                   'error_name': str(school_id),
                                   'error_info': "No such school"},
                                  context_instance=RequestContext(request))

    # Implicitly, it must now exist. Here, check whether it is a POST or otherwise request.
    if request.method == 'POST':
        form = SchoolForm(request.POST, instance=school)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return render_to_response('error.html',
                                          {'error_type': "School",
                                           'error_name': "[" + form.cleaned_data['name'] + "]",
                                           'error_info': "School name cannot be validated, most likely a non-existent school"},
                                          context_instance=RequestContext(request))

            return render_to_response('thanks.html',
                                      {'data_type': "School",
                                       'data_name': "[" + form.cleaned_data['name'] + "]"},
                                      context_instance=RequestContext(request))

    # This section is the code which is called when you want to view the some school in MIT-TAB
    else:
        form = SchoolForm(instance=school)
        links = [('/school/' + str(school_id) + '/delete/', 'Delete', True)]  # include a link to delete the school
        return render_to_response('data_entry.html',
                                  {'form': form,
                                   'links': links,
                                   'title': "Viewing School: %s" % (school.name)},
                                  context_instance=RequestContext(request))


def enter_school(request):
    """
    Allows for you to enter a school. If it is a POST request, it will try to save the form. If that fails, it will
    throw an error. If the form is invalid, it will jump to the bottom and return a new form to create a school.
    Similarly, if this is a GET request, it will return a new form to enter a school.
    """
    if request.method == 'POST':
        form = SchoolForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                error_info = "School name cannot be validated, most likely a duplicate school."
                return render_to_response('error.html',
                                          {'error_type': "School", 'error_name': "[" + form.cleaned_data['name'] + "]",
                                           'error_info': error_info},
                                          context_instance=RequestContext(request))

            return render_to_response('thanks.html',
                                      {'data_type': "School",
                                       'data_name': "[" + form.cleaned_data['name'] + "]",
                                       'data_modification': "CREATED",
                                       'enter_again': True},
                                      context_instance=RequestContext(request))

    else:
        form = SchoolForm()

    return render_to_response('data_entry.html',
                              {'form': form, 'title': "Create School"},
                              context_instance=RequestContext(request))


@permission_required('tab.school.can_delete', login_url="/403/")
def delete_school(request, school_id):
    """
    Deletes a school given some school_id.
    :param school_id: is the ID of the school in the program. It is a numerical value and _not_ simply the same as the
        school name. You can view the school_id by clicking on the school and checking the URL. It is _not_ the same as
        the number accompanying the school on the index page.
    """

    error_msg = None
    try:
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
                               'data_name': "[" + str(school_id) + "]",
                               'data_modification': 'DELETED'},
                              context_instance=RequestContext(request))


# End of School views

'''
Room views.
Includes views for viewing rooms, viewing a room, entering a room, and deleting a room. Deletion thereof requires
permission.
'''


def view_rooms(request):
    """
    Allows for you to view all the rooms. This is what is returned when you click on the 'Room List' button on the
    index page. It also filters to include or exclude those rooms with a rank of 0.
    """

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

    return render_to_response('list_data.html',
                              {'item_type': 'room',
                               'title': "Viewing All Rooms",
                               'item_list': all_rooms,
                               'symbol_text': symbol_text,
                               "filters": filters},
                              context_instance=RequestContext(request))


def view_room(request, room_id):
    """
    Quite similar to the view_school one. It checks whether the Room exists, then it provides the room. If it is a POST
    request, it saves the room (throws an error otherwise). If it is not a POST request, it displays the room.
    :param room_id: This room_id is the integer id of the room. You can view it by clicking on the room and checking
        the URL for further information.
    """
    room_id = int(room_id)
    try:
        room = Room.objects.get(pk=room_id)
    except Room.DoesNotExist:
        return render_to_response('error.html',
                                  {'error_type': "View Room",
                                   'error_name': str(room_id),
                                   'error_info': "No such room"},
                                  context_instance=RequestContext(request))

    if request.method == 'POST':
        form = RoomForm(request.POST, instance=room)
        if form.is_valid():

            try:
                form.save()
            except ValueError:
                err_msg = "Room name cannot be validated, most likely a non-existent room"
                return render_to_response('error.html',
                                          {'error_type': "Room",
                                           'error_name': "[" + form.cleaned_data['name'] + "]",
                                           'error_info': err_msg},
                                          context_instance=RequestContext(request))

            return render_to_response('thanks.html',
                                      {'data_type': "Room",
                                       'data_name': "[" + form.cleaned_data['name'] + "]"},
                                      context_instance=RequestContext(request))

    # the code here displays the room, creates a form with the Room instance loaded. returns, and also includes a link
    # to delete the room if necessary
    else:

        form = RoomForm(instance=room)
        links = [('/room/' + str(room_id) + '/delete/', 'Delete', True)]
        return render_to_response('data_entry.html',
                                  {'form': form,
                                   'links': links,
                                   'title': "Viewing Room: %s" % (room.name)},
                                  context_instance=RequestContext(request))


def enter_room(request):
    """
    Allows you to enter a room into the system. See 'enter_school' for how it works, because it works the exact same way
    as the 'enter_school' method.
    """
    if request.method == 'POST':
        form = RoomForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return render_to_response('error.html',
                                          {'error_type': "Room", 'error_name': "[" + form.cleaned_data['name'] + "]",
                                           'error_info': "Room name cannot be validated, most likely a duplicate room"},
                                          context_instance=RequestContext(request))

            return render_to_response('thanks.html',
                                      {'data_type': "Room",
                                       'data_name': "[" + form.cleaned_data['name'] + "]",
                                       'data_modification': "CREATED",
                                       'enter_again': True},
                                      context_instance=RequestContext(request))

    else:
        form = RoomForm()
    return render_to_response('data_entry.html',
                              {'form': form, 'title': 'Create Room'},
                              context_instance=RequestContext(request))


@permission_required('tab.room.can_delete', login_url="/403/")
def delete_room(request, room_id):
    """
    Deletes a room. Requires permissions.
    :param room_id: Integer identifier for the room.
    """
    room_id = int(room_id)  # cast to int
    try:
        r = Room.objects.get(pk=room_id)
        r.delete()
    except Room.DoesNotExist:
        return render_to_response('error.html',
                                  {'error_type': "Delete Room",
                                   'error_name': str(room_id),
                                   'error_info': "This room does not exist, please try again with a valid id. "},
                                  context_instance=RequestContext(request))
    return render_to_response('thanks.html',
                              {'data_type': "Room",
                               'data_name': "[" + str(room_id) + "]",
                               'data_modification': 'DELETED'},
                              context_instance=RequestContext(request))


'''
Scratch views.
Includes three views for adding, deleting, and viewing scratches.
'''


# View for manually adding scratches
# TODO: Update logic here (and all other scratch methods) to prevent duplicate scratches
def add_scratch(request):
    """
    If the form is being posted, save. If it is not being posted, return a new form.
    """

    if request.method == 'POST':
        form = ScratchForm(request.POST)
        if form.is_valid():
            # TODO here would probably be the best place to add verification logic. It would probably be best to include
            # identification logic to prevent teams from scratching people they have tab-scratched and also prevent the
            # normal duplication errors.
            form.save()

        judge = form.cleaned_data['judge'].name
        team = form.cleaned_data['team'].name
        return render_to_response('thanks.html',
                                  {'data_type': "Scratch",
                                   'data_name': " from {0} on {1}".format(team, judge),
                                   'data_modification': "CREATED",
                                   'enter_again': True},
                                  context_instance=RequestContext(request))

    else:
        form = ScratchForm(initial={'scratch_type': 0})
    return render_to_response('data_entry.html',
                              {'title': "Adding Scratch",
                               'form': form},
                              context_instance=RequestContext(request))


@permission_required('tab.scratch.can_delete', login_url="/403/")
def delete_scratch(request, item_id, scratch_id):
    """
    Deletes a scratch given some integer 'scratch_id'.
    :param scratch_id: Some integer scratch id number. Cannot be viewed by URLs, as clicking on any scratch simply links
        to the team which issued the scratch the first place.
    """
    scratch_id = int(scratch_id)
    try:
        scratch = Scratch.objects.get(pk=scratch_id)
        scratch.delete()

    except Scratch.DoesNotExist:
        return render_to_response('error.html',
                                  {'error_type': "Delete Scratch",
                                   'error_name': str(scratch_id),
                                   'error_info': "This scratch does not exist, please try again with a valid id. "},
                                  context_instance=RequestContext(request))

    return render_to_response('thanks.html',
                              {'data_type': "Scratch",
                               'data_name': str(scratch_id),
                               'data_modification': 'DELETED'},
                              context_instance=RequestContext(request))


def view_scratches(request):
    """
    Provides a list of all the scratches. If you click the scratch tab in MIT-TAB, you will see that it provides this in
    the manner: 'TEAM_NAME <=TYPE=> JUDGE_NAME'.
    """
    c_scratches = [(s.team.pk, str(s)) for s in Scratch.objects.all()]
    return render_to_response('list_data.html',
                              {'item_type': 'team',
                               'title': "Viewing All Scratches for Teams",
                               'item_list': c_scratches}, context_instance=RequestContext(request))


# End Scratches
'''
Functions for batch data import from Excel files. See mittab.libs.data_import
'''


def upload_data(request):
    """
    Look at the UploadDataForm to find the input form. From there, there are three files whcih are provided, each one is
    checked for its existence first. Then, it is imported. Check the important logic in mittab.libs.data_import to see
    exactly how that data is imported from Excel files.

    The program first looks at whether it is a POST request. If it is not, then it returns a new form.
    """
    if request.method == 'POST':
        form = UploadDataForm(request.POST, request.FILES)
        if form.is_valid():
            importName = ''
            results = ''

            # If there is a team file, import.
            if 'team_file' in request.FILES:
                team_errors = import_teams.import_teams(request.FILES['team_file'])
                importName += request.FILES['team_file'].name + ' '
                if len(team_errors) > 0:
                    results += 'Team Import Errors (Please Check These Manually):\n'
                    for e in team_errors:
                        results += '\t' + e + '\n'

            # If there is a judge, import.
            if 'judge_file' in request.FILES:
                judge_errors = import_judges.import_judges(request.FILES['judge_file'])
                importName += request.FILES['judge_file'].name + ' '
                if len(judge_errors) > 0:
                    results += 'Judge Import Errors (Please Check These Manually):\n'
                    for e in judge_errors:
                        results += '\t' + e + '\n'

            # If there is a room file, import.
            if 'room_file' in request.FILES:
                room_errors = import_rooms.import_rooms(request.FILES['room_file'])
                importName += request.FILES['room_file'].name + ' '
                if len(room_errors) > 0:
                    results += 'Room Import Errors (Please Check These Manually):\n'
                    for e in room_errors:
                        results += '\t' + e + '\n'

            # Render a success message. Include information on errors which may have occurred.
            return render_to_response('thanks.html',
                                      {'data_type': "Database data",
                                       'data_name': importName,
                                       'data_modification': "INPUT",
                                       'results': True,
                                       'data_results': results},
                                      context_instance=RequestContext(request))
    else:
        form = UploadDataForm()
    return render_to_response('data_entry.html',
                              {'form': form,
                               'title': 'Upload Input Files'},
                              context_instance=RequestContext(request))
