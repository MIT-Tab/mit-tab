#Copyright (C) 2011 by Julia Boortz and Joseph Lynch

#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:

#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.

#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.

from mittab.apps.tab.models import *
from mittab.apps.tab.forms import RoomForm

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
        try:
            sh.cell(num_rooms,0).value
            num_rooms +=1
        except IndexError:
            found_end = True

        #Verify sheet has required number of columns
        try:
            sh.cell(0, 1).value
        except:
            room_errors.append("ERROR: Insufficient Columns in Sheet. No Data Read")
            return room_errors

    for i in range(1, num_rooms):
        room_name = sh.cell(i,0).value
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
        room_rank = sh.cell(i,1).value
        try:
            room_rank = Decimal(room_rank)
        except:
            room_errors.append(room_name + ": Rank not number")
            continue
        if room_rank > 100 or room_rank < 0:
            room_errors.append(room_name + ": Rank should be between 0-100")
            continue

        #Create the room
        room = Room(name=room_name, rank=room_rank);
        try:
            room.save()
        except:
            room_errors.append(room_name + ": Unknown Error")

    return room_errors
