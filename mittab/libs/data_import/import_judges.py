from mittab.apps.tab.models import School
from mittab.apps.tab.forms import JudgeForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException


def normalize_email(value):
    return value.strip() or None


def import_judges(file_to_import, created_by=None):
    try:
        workbook = Workbook(file_to_import, 2)
    except InvalidWorkbookException:
        return ["Judges file is not a valid .xlsx file"]
    return JudgeImporter(workbook, created_by=created_by).import_data()


class JudgeImporter(WorkbookImporter):
    def import_row(self, row, row_number):
        judge_name = row[0]
        judge_email = normalize_email(row[1])
        judge_rank = row[2]

        try:
            judge_rank = round(float(judge_rank), 2)
        except ValueError:
            self.error("Judge rank is not a number", row_number)

        schools = []
        for school_name in row[3:]:
            if not school_name:
                continue
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

        data = {
            "name": judge_name,
            "email": judge_email,
            "rank": judge_rank,
            "schools": schools,
        }
        form = JudgeForm(data=data)
        if form.is_valid():
            self.create(form)
        else:
            for _field, error_msgs in form.errors.items():
                for error_msg in error_msgs:
                    self.error(f"{judge_name} - {error_msg}", row_number)
