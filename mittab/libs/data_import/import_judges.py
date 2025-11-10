from mittab.apps.tab.models import School
from mittab.apps.tab.forms import JudgeForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException


def import_judges(file_to_import):
    try:
        workbook = Workbook(file_to_import, 2)
    except InvalidWorkbookException:
        return ["Judges file is not a valid .xlsx file"]
    return JudgeImporter(workbook).import_data()


class JudgeImporter(WorkbookImporter):
    def import_row(self, row, row_number):
        judge_name = row[0]
        judge_rank = row[1]

        try:
            judge_rank = round(float(judge_rank), 2)
        except ValueError:
            self.error("Judge rank is not a number", row)

        schools = []
        for school_name in row[2:]:
            school_query = School.objects.filter(name__iexact=school_name)
            if school_query.exists():
                school = school_query.first()
            else:
                school = School(name=school_name)
                try:
                    self.create(school)
                except Exception:
                    self.error(f"Invalid school '{school_name}'", row_number)
            schools.append(school.id)

        data = {"name": judge_name, "rank": judge_rank, "schools": schools}
        form = JudgeForm(data=data)
        if form.is_valid():
            self.create(form)
        else:
            for _field, error_msgs in form.errors.items():
                for error_msg in error_msgs:
                    self.error(f"{judge_name} - {error_msg}", row_number)
