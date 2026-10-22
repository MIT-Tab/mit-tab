from django.core.exceptions import ValidationError

from mittab.apps.tab.models import School, Team, Debater
from mittab.apps.tab.forms import DebaterForm, TeamForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException


def normalize_email(value):
    return value.strip() or None


def import_teams(file_to_import, created_by=None):
    try:
        workbook = Workbook(file_to_import)
    except InvalidWorkbookException as exc:
        return [str(exc)]
    return TeamImporter(workbook, created_by=created_by).import_data()


class TeamImporter(WorkbookImporter):
    kind = "teams"

    novice_values = ["n", "nov", "novice"]
    full_seed_values = ["full", "full seed"]
    half_seed_values = ["half", "half seed"]
    free_seed_values = ["free", "free seed"]
    unseeded_values = ["unseeded", "un", "none", ""]

    def import_row(self, row, row_number):
        team_name = row[0]
        school_name = row[1]
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

        hybrid_school_name = row[2]
        hybrid_school = None
        if hybrid_school_name:
            hybrid_school_query = School.objects.filter(name__iexact=hybrid_school_name)
            if hybrid_school_query.exists():
                hybrid_school = hybrid_school_query.first()
            else:
                hybrid_school = School(name=hybrid_school_name)
                try:
                    self.create(hybrid_school)
                except ValidationError as exc:
                    self.error(
                        f'Value "{hybrid_school_name}" in column "Hybrid school": '
                        + "; ".join(exc.messages),
                        row_number,
                    )
                    return

        team_seed = row[3].strip().lower()
        if team_seed in self.full_seed_values:
            team_seed = Team.FULL_SEED
        elif team_seed in self.half_seed_values:
            team_seed = Team.HALF_SEED
        elif team_seed in self.free_seed_values:
            team_seed = Team.FREE_SEED
        elif team_seed in self.unseeded_values:
            team_seed = Team.UNSEEDED
        else:
            self.error(
                f'Value "{row[3]}" in column "Seed": '
                "expected full, half, free, or unseeded.",
                row_number,
            )
            return

        for column in (5, 9):
            if row[column].strip().lower() not in self.novice_values + [
                "",
                "v",
                "var",
                "varsity",
            ]:
                self.error(
                    f'Value "{row[column]}" in column '
                    f'"Debater {1 if column == 5 else 2} status": '
                    "expected novice, varsity, or a blank cell.",
                    row_number,
                )
                return

        deb1_name = row[4]
        deb1_status = row[5].strip().lower()
        if deb1_status in self.novice_values:
            deb1_status = Debater.NOVICE
        else:
            deb1_status = Debater.VARSITY
        deb1_email = normalize_email(row[6])
        deb1_apda_id = row[7].strip() or -1
        deb2_name = row[8]
        deb2_status = row[9].strip().lower()
        deb2_email = normalize_email(row[10])
        deb2_apda_id = row[11].strip() or -1

        if deb2_status in self.novice_values:
            deb2_status = Debater.NOVICE
        else:
            deb2_status = Debater.VARSITY

        deb1_form = DebaterForm(
            data={
                "name": deb1_name,
                "novice_status": deb1_status,
                "email": deb1_email,
                "apda_id": deb1_apda_id,
            }
        )
        if deb1_form.is_valid():
            self.create(deb1_form)
        else:
            self.form_errors(
                deb1_form,
                {"name": 4, "novice_status": 5, "email": 6, "apda_id": 7},
                row,
                row_number,
            )
            return

        deb2_form = DebaterForm(
            data={
                "name": deb2_name,
                "novice_status": deb2_status,
                "email": deb2_email,
                "apda_id": deb2_apda_id,
            }
        )
        if deb2_form.is_valid():
            self.create(deb2_form)
        else:
            self.form_errors(
                deb2_form,
                {"name": 8, "novice_status": 9, "email": 10, "apda_id": 11},
                row,
                row_number,
            )
            return

        team_form = TeamForm(
            data={
                "name": team_name,
                "school": school.id,
                "hybrid_school": hybrid_school and hybrid_school.id,
                "debaters": [deb1_form.instance.id, deb2_form.instance.id],
                "seed": team_seed,
                "break_preference": Team.VARSITY,
            }
        )

        if team_form.is_valid():
            team_form.instance.ranking_public = True
            self.create(team_form)
        else:
            self.form_errors(
                team_form,
                {"name": 0, "school": 1, "hybrid_school": 2, "seed": 3},
                row,
                row_number,
            )
            return
