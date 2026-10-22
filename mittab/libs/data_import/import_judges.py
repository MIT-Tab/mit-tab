from django.core.exceptions import ValidationError

from mittab.libs.spreadsheets import is_number
from mittab.apps.tab.models import School, JudgeExpectedCheckIn, TabSettings
from mittab.apps.tab.forms import JudgeForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException


def normalize_email(value):
    return value.strip() or None


def import_judges(file_to_import, created_by=None):
    try:
        workbook = Workbook(file_to_import)
    except InvalidWorkbookException as exc:
        return [str(exc)]
    return JudgeImporter(workbook, created_by=created_by).import_data()


class JudgeImporter(WorkbookImporter):
    kind = "judges"

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

    false_expectation_values = {"", "0", "false", "no", "n", "out"}

    def _split_row(self, row):
        num_rounds = TabSettings.get("tot_rounds", 5)
        if (
            num_rounds
            and len(row) >= 3 + num_rounds
            and (
                getattr(self.workbook, "named_headers", False)
                or all(
                    cell.strip().lower()
                    in self.truthy_expectation_values | self.false_expectation_values
                    for cell in row[-num_rounds:]
                )
            )
        ):
            expectation_cells = row[-num_rounds:]
            school_cells = row[3:-num_rounds]
        else:
            expectation_cells = []
            school_cells = row[3:]
        return school_cells, expectation_cells

    def _expected_rounds(self, expectation_cells, row_number):
        expected_rounds = []
        for index, cell in enumerate(expectation_cells, start=1):
            normalized = str(cell or "").strip().lower()
            if normalized in self.truthy_expectation_values:
                expected_rounds.append(index)
            elif normalized not in self.false_expectation_values:
                self.error(
                    f'Value "{cell}" in column "Round {index} expected": '
                    "use yes, no, or a blank cell.",
                    row_number,
                )
                return None
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

        if not is_number(judge_rank):
            self.error(
                f'Value "{judge_rank}" in column "Calibration": '
                "expected a finite number.",
                row_number,
            )
            return
        judge_rank = round(float(judge_rank), 2)

        school_cells, expectation_cells = self._split_row(row)
        expected_rounds = self._expected_rounds(expectation_cells, row_number)
        if expected_rounds is None:
            return

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
                except ValidationError as exc:
                    self.error(
                        f'Value "{school_name}" in column "School": '
                        + "; ".join(exc.messages),
                        row_number,
                    )
                    return
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
            if expected_rounds:
                self._apply_expected_rounds(judge, expected_rounds)
        else:
            self.form_errors(form, {"name": 0, "email": 1, "rank": 2}, row, row_number)
