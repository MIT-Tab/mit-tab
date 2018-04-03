from django.shortcuts import render_to_response
from django.template import RequestContext
from django.contrib.auth.decorators import permission_required

from forms import RoomForm
from models import *

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

    return render_to_response('list_data.html', 
                             {'item_type':'room',
                              'title': "Viewing All Rooms",
                              'item_list':all_rooms,
                              'symbol_text':symbol_text,
                              "filters": filters},
                              context_instance=RequestContext(request))

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
        return render_to_response('data_entry.html', 
                                 {'form': form,
                                  'links': [],
                                  'title': "Viewing Room: %s"%(room.name)}, 
                                 context_instance=RequestContext(request))

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

