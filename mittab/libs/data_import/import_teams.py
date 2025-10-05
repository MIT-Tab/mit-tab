from mittab.apps.tab.models import School, Team, Debater
from mittab.apps.tab.forms import DebaterForm, TeamForm
from mittab.libs.data_import import Workbook, WorkbookImporter, InvalidWorkbookException


def import_teams(file_to_import):
    try:
        workbook = Workbook(file_to_import, 8)
    except InvalidWorkbookException:
        return ["Teams file is not a valid .xlsx file"]
    return TeamImporter(workbook).import_data()


class TeamImporter(WorkbookImporter):
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
            except Exception:
                self.error("Invalid school '%s'" % school_name, row_number)
                return

        hybrid_school_name = row[2]
        hybrid_school = None
        if hybrid_school_name:
            hybrid_school_query = School.objects.filter(
                name__iexact=hybrid_school_name)
            if hybrid_school_query.exists():
                hybrid_school = hybrid_school_query.first()
            else:
                hybrid_school = School(name=hybrid_school_name)
                try:
                    self.create(hybrid_school)
                except Exception:
                    self.error(
                        "Invalid hybrid school '%s'" % hybrid_school_name,
                        row_number)
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
            self.error("Invalid seed value for team %s" % team_name,
                       row_number)
            return

        deb1_name = row[4]
        deb1_status = row[5].strip().lower()
        if deb1_status in self.novice_values:
            deb1_status = Debater.NOVICE
        else:
            deb1_status = Debater.VARSITY
        deb1_apda_id = row[6].strip() or -1
        deb2_name = row[7]
        deb2_status = row[8].strip().lower()
        if deb2_status in self.novice_values:
            deb2_status = Debater.NOVICE
        else:
            deb2_status = Debater.VARSITY
        deb2_apda_id = row[9].strip() or -1

        deb1_form = DebaterForm(data={
            "name": deb1_name,
            "novice_status": deb1_status,
            "apda_id": deb1_apda_id
        })
        if deb1_form.is_valid():
            self.create(deb1_form)
        else:
            for _field, error_msgs in deb1_form.errors.items():
                for error_msg in error_msgs:
                    self.error("%s - %s" % (deb1_name, error_msg), row_number)
            return

        deb2_form = DebaterForm(data={
            "name": deb2_name,
            "novice_status": deb2_status,
            "apda_id": deb2_apda_id
        })
        if deb2_form.is_valid():
            self.create(deb2_form)
        else:
            for _field, error_msgs in deb2_form.errors.items():
                for error_msg in error_msgs:
                    self.error("%s - %s" % (deb2_name, error_msg), row_number)
            return

        team_form = TeamForm(
            data={
                "name": team_name,
                "school": school.id,
                "hybrid_school": hybrid_school and hybrid_school.id,
                "debaters": [deb1_form.instance.id, deb2_form.instance.id],
                "seed": team_seed,
                "break_preference": Team.VARSITY
            })

        if team_form.is_valid():
            self.create(team_form)
        else:
            for _field, error_msgs in team_form.errors.items():
                for error_msg in error_msgs:
                    self.error("%s - %s" % (deb2_name, error_msg), row_number)
            return
