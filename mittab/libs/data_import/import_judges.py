from mittab.apps.tab.models import School, JudgeExpectedCheckIn, TabSettings
from mittab.apps.tab.forms import JudgeForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException


def normalize_email(value):
    return value.strip() or None


def import_judges(file_to_import, created_by=None):
    try:
        workbook = Workbook(file_to_import, 3)
    except InvalidWorkbookException:
        return ["Judges file is not a valid .xlsx file"]
    return JudgeImporter(workbook, created_by=created_by).import_data()


class JudgeImporter(WorkbookImporter):
    truthy_expectation_values = {
        "1",
        "true",
        "yes",
        "y",
        "expected",
        "in",
        "x",
        "check",
        "checked",
    }

    def _split_row(self, row):
        num_rounds = TabSettings.get("tot_rounds", 5)
        if num_rounds and len(row) >= 3 + num_rounds:
            expectation_cells = row[-num_rounds:]
            school_cells = row[3:-num_rounds]
        else:
            expectation_cells = []
            school_cells = row[3:]
        return school_cells, expectation_cells

    def _expected_rounds(self, expectation_cells):
        expected_rounds = []
        for index, cell in enumerate(expectation_cells, start=1):
            normalized = str(cell or "").strip().lower()
            if normalized and normalized in self.truthy_expectation_values:
                expected_rounds.append(index)
        return expected_rounds

    def _apply_expected_rounds(self, judge, expected_rounds):
        for round_number in expected_rounds:
            self.create(
                JudgeExpectedCheckIn(
                    judge=judge,
                    round_number=round_number,
                )
            )

    def import_row(self, row, row_number):
        judge_name = row[0]
        judge_email = normalize_email(row[1])
        judge_rank = row[2]

        try:
            judge_rank = round(float(judge_rank), 2)
        except ValueError:
            self.error("Judge rank is not a number", row_number)

        school_cells, expectation_cells = self._split_row(row)

        schools = []
        for school_name in school_cells:
            school_name = (school_name or "").strip()
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
            judge = form.instance
            expected_rounds = self._expected_rounds(expectation_cells)
            if expected_rounds:
                self._apply_expected_rounds(judge, expected_rounds)
        else:
            for _field, error_msgs in form.errors.items():
                for error_msg in error_msgs:
                    self.error(f"{judge_name} - {error_msg}", row_number)
