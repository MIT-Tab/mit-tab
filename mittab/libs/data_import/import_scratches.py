from mittab.apps.tab.models import Team, Judge, Scratch
from mittab.apps.tab.forms import ScratchForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException


def import_scratches(file_to_import):
    try:
        workbook = Workbook(file_to_import, 3)
    except InvalidWorkbookException:
        return ["Scratches file is not a valid .xlsx file"]
    return ScratchImporter(workbook).import_data()


class ScratchImporter(WorkbookImporter):
    team_scratch_values = ["team scratch", "team"]
    tab_scratch_values = ["tab scratch", "tab"]

    def import_row(self, row, row_number):
        team_name = row[0]
        judge_name = row[1]
        scratch_type = row[2]

        got_error = False
        if not Team.objects.filter(name=team_name).exists():
            self.error(f"Team '{team_name}' does not exist", row_number)
            got_error = True
        if not Judge.objects.filter(name=judge_name).exists():
            self.error(f"Judge '{judge_name}' does not exist", row_number)
            got_error = True

        if scratch_type.strip().lower() in self.team_scratch_values:
            scratch_type = Scratch.TEAM_SCRATCH
        elif scratch_type.strip().lower() in self.tab_scratch_values:
            scratch_type = Scratch.TAB_SCRATCH
        else:
            got_error = True
            self.error(f"'{scratch_type}' is not a valid scratch type",
                       row_number)
        if got_error:
            return

        form = ScratchForm(
            data={
                "scratch_type": scratch_type,
                "team": Team.objects.get(name=team_name).id,
                "judge": Judge.objects.get(name=judge_name).id
            })
        if form.is_valid():
            self.create(form)
        else:
            for _field, error_msgs in form.errors.items():
                for error_msg in error_msgs:
                    self.error(
                        f"{team_name} x {judge_name} - {error_msg}",
                        row_number)
