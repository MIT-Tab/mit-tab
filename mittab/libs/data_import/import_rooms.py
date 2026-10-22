from mittab.libs.spreadsheets import is_number
from mittab.apps.tab.forms import RoomForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException


def import_rooms(file_to_import):
    try:
        workbook = Workbook(file_to_import)
    except InvalidWorkbookException as exc:
        return [str(exc)]
    return RoomImporter(workbook).import_data()


class RoomImporter(WorkbookImporter):
    kind = "rooms"

    def import_row(self, row, row_number):
        room_name = row[0]
        room_rank = row[1]

        if not is_number(room_rank):
            self.error(
                f'Value "{room_rank}" in column "Rank": expected a finite number.',
                row_number,
            )
            return
        room_rank = float(room_rank)

        form = RoomForm(data={"name": room_name, "rank": room_rank})
        if form.is_valid():
            self.create(form)
        else:
            self.form_errors(form, {"name": 0, "rank": 1}, row, row_number)
