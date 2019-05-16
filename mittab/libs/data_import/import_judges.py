from mittab.apps.tab.models import School
from mittab.apps.tab.forms import JudgeForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException

from decimal import *
import xlrd


def import_judges(file_to_import):
    try:
        workbook = Workbook(file_to_import)
    except InvalidWorkbookException:
        return ["Judges file is not a valid .xlsx file"]
    return JudgeImporter(workbook).import_data().errors


class JudgeImporter(WorkbookImporter):
    min_row_size = 2
    name = "Judge Importer"

    def import_row(self, row):
        judge_name = row[0]
        judge_rank = row[1]

        try:
            judge_rank = round(float(judge_rank), 2)
        except ValueError:
            self.error("Judge rank is not a number", row)

        col = 2
        schools = []
        for school_name in row[2:]:
            school_query = School.objects.filter(name__iexact=school_name)
            if school_query.exists():
                school = school_query.first()
            else:
                school = School(name=school_name)
                try:
                    self.create(school)
                except:
                    self.error("Invalid school '%s'" % school_name, row)
            schools.append(str(school.id))

        data = {"name": judge_name, "rank": judge_rank, "schools": schools}
        form = JudgeForm(data=data)
        if form.is_valid():
            self.create(form)
        else:
            for error_msg in form.errors.items():
                self.error("%s - %s", (judge_name, error_msg), row)
