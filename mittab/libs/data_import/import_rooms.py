from mittab.apps.tab.forms import RoomForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException


def import_rooms(file_to_import):
    try:
        workbook = Workbook(file_to_import, 2)
    except InvalidWorkbookException:
        return ["Rooms file is not a valid .xlsx file"]
    return RoomImporter(workbook).import_data()


class RoomImporter(WorkbookImporter):
    file_label = "rooms"
    expected_headers = [
        ("Room", ("room", "room name")),
        ("Rank", ("rank", "room rank")),
    ]

    def import_row(self, row, row_number):
        room_name = row[0]
        room_rank = row[1]

        try:
            room_rank = float(room_rank)
        except ValueError:
            self.error(
                f"Room {room_name} has invalid rank '{room_rank}'",
                row_number)
            return

        form = RoomForm(data={"name": room_name, "rank": room_rank})
        if form.is_valid():
            self.create(form)
        else:
            for _field, error_msgs in form.errors.items():
                for error_msg in error_msgs:
                    self.error(f"{room_name} - {error_msg}", row_number)
