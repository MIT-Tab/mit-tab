from mittab.apps.tab.forms import RoomForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException


def import_rooms(file_to_import):
    try:
        workbook = Workbook(file_to_import, 2)
    except InvalidWorkbookException:
        return ["Rooms file is not a valid .xslx file"]
    return RoomImporter(workbook).import_data()


class RoomImporter(WorkbookImporter):
    name = "Room Importer"

    def import_row(self, row, row_number):
        room_name = row[0]
        room_rank = row[1]

        try:
            room_rank = float(room_rank)
        except ValueError:
            self.error.append("Room %s has invalid rank '%s'" % (room_name, room_rank),
                    row_number)
            return

        room_form = RoomForm(data={"name": room_name, "rank": room_rank})
        if room_form.is_valid():
            self.create(room_form)
        else:
            for _field, error_msgs in form.errors.items():
                for error_msg in error_msgs:
                    self.error("%s - %s" % (room_name, error_msg), row_number)
