from mittab.apps.tab.models import School
from mittab.apps.tab.forms import JudgeForm
from mittab.libs.data_import import WorkbookImporter

from decimal import *
import xlrd


def import_judges(file_to_import):
    try:
        return JudgeImporter(file_to_import).import_data().errors
    except Exception:
        return ["Judges file is not a valid .xlsx file"]


class JudgeImporter(WorkbookImporter):
    min_row_size : int = 1
    name : str = "Judge Importer"

    def import_row(self, row):
        judge_name = self._get(row, 0)
        judge_rank = self._get(row, 1)

        try:
            judge_rank = round(float(judge_rank), 2)
        except ValueError:
            self.error("Judge rank is not a number", row)

        col = 2
        schools = []
        while self._get(row, col) not in ["", None]:
            school_name = self._get(row, col)
            col += 1
            school_query = School.objects.filter(name__iexact=school_name)

            if school_query.exists():
                school = school_query.first()
            else:
                school = School(name=school_name)
                try:
                    self.create(school)
                except:
                    self.error("Invalid school '%s'" % school_name, row)
            schools.append(school.id)
        data = {'name': judge_name, 'rank': judge_rank, 'schools': schools}
        form = JudgeForm(data=data)
        if form.is_valid():
            self.create(form.instance)
        else:
            for error_msg in form.errors.items():
                self.error("%s - %s", (judge_name, error_msg), row)
