# Copyright (C) 2011 by Julia Boortz and Joseph Lynch

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

from decimal import *
import xlrd

from mittab.apps.tab.models import *


def import_rooms(import_file, using_overwrite=False):
    try:
        sh = xlrd.open_workbook(filename=None, file_contents=import_file.read()).sheet_by_index(0)
    except:
        return ["ERROR: Please upload an .xlsx file. This filetype is not compatible"]
    num_rooms = 0
    found_end = False
    room_errors = []

    while not found_end:
        try:
            sh.cell(num_rooms, 0).value
            num_rooms += 1
        except IndexError:
            found_end = True

        # Verify sheet has required number of columns
        try:
            sh.cell(0, 1).value
        except:
            room_errors.append("ERROR: Insufficient Columns in Sheet. No Data Read")
            return room_errors

    for i in range(1, num_rooms):

        # headers are
        # name, room rank

        room_name = sh.cell(i, 0).value
        if room_name == '':
            room_errors.append("Row " + str(i) + ": Empty room name")
            continue

        duplicate = False
        if Room.objects.filter(name=room_name).exists():  # check for duplicates
            room_errors.append(room_name + ': Duplicate room name')
            duplicate = True

        # Load and validate room_rank
        room_rank = sh.cell(i, 1).value
        try:
            # auto-round to two floating point digits
            room_rank = round(Decimal(room_rank), 2)
        except TypeError or ValueError:
            room_errors.append(room_name + ": Rank in file is not a number")
            continue

        # cap room rank at 100
        if room_rank >= 100:
            room_errors.append(room_name + ": is above 100, fix")
            continue

        # floor room rank at 0
        if room_rank < 0:
            room_errors.append(room_name + ": is below 0, fix")
            continue

        # save
        if not duplicate:  # Create the room
            room = Room(name=room_name, rank=room_rank)

        else:  # else, update room
            if using_overwrite:
                room = Room.objects.get(name=room_name)
                room.rank = room_rank

                try:
                    room.save()
                except:
                    room_errors.append(room_name + ": Error in saving")

            else:
                # do nothing
                pass

    return room_errors
