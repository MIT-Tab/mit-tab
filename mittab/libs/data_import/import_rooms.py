from mittab.apps.tab.models import *
from mittab.apps.tab.forms import RoomForm
from mittab.libs.data_import import value_or_empty

from decimal import *
import xlrd
from xlwt import Workbook

def import_rooms(fileToImport):
    try:
        sh = xlrd.open_workbook(filename=None, file_contents=fileToImport.read()).sheet_by_index(0)
    except:
        return ["ERROR: Please upload an .xlsx file. This filetype is not compatible"]
    num_rooms = 0
    found_end = False
    room_errors = []

    while found_end == False:
        room_name = value_or_empty(sh, num_rooms, 0)
        if room_name:
            num_judges += 1
        else:
            found_end = True

        #Verify sheet has required number of columns
        try:
            sh.cell(0, 1).value
        except:
            room_errors.append("ERROR: Insufficient Columns in Sheet. No Data Read")
            return room_errors

    for i in range(1, num_rooms):
        room_name = sh.cell(i, 0).value
        if room_name == '':
            room_errors.append("Row " + str(i) + ": Empty Room Name")
            continue
        try:
            Room.objects.get(name=room_name)
            room_errors.append(room_name + ': Duplicate Room Name')
            continue
        except:
            pass

        #Load and validate room_rank
        room_rank = sh.cell(i, 1).value
        room_string = str(room_rank)
        try:
            room_rank = Decimal(room_rank)
        except:
            room_errors.append(room_name + ": Rank not number")
            continue
        if len(room_string) > 5 or (room_rank < 10 and len(room_string) > 4):
            room_errors.append(room_name + ": Rank should have no more than two decimal places")
            continue
        if room_rank >= 100 or room_rank < 0:
            room_errors.append(room_name + ": Rank should be between 0-99.99")
            continue

        #Create the room
        room = Room(name=room_name, rank=room_rank);
        try:
            room.save()
        except:
            room_errors.append(room_name + ": Unknown Error")

    return room_errors
