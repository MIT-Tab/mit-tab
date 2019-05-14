import xlrd

from mittab.apps.tab.models import Team, Debater, School
from mittab.apps.tab.forms import SchoolForm


def import_teams(file_to_import):
    try:
        sheet = xlrd.open_workbook(
            filename=None, file_contents=file_to_import.read()
        ).sheet_by_index(0)
    except Exception:
        return ["Please upload an .xlsx file. This filetype is not compatible"]
    num_teams = 0
    found_end = False
    team_errors = []
    while found_end == False:
        try:
            sheet.cell(num_teams, 0).value
            num_teams += 1
        except IndexError:
            found_end = True

        # Verify sheet has required number of columns
        try:
            sheet.cell(0, 5).value
        except Exception:
            team_errors.append("ERROR: Insufficient Columns in Sheet. No Data Read")
            return team_errors

    for i in range(1, num_teams):

        team_name = sheet.cell(i, 0).value
        if team_name == "":
            team_errors.append("Row " + str(i) + ": Empty Team Name")
            continue
        try:
            Team.objects.get(name=team_name)
            team_errors.append(team_name + ": Duplicate Team Name")
            continue
        except Exception:
            pass

        school_name = sheet.cell(i, 1).value.strip()
        try:
            team_school = School.objects.get(name__iexact=school_name)
        except Exception:
            # Create school through SchoolForm because for some reason they
            # don't save otherwise
            form = SchoolForm(data={"name": school_name})
            if form.is_valid():
                form.save()
            else:
                team_errors.append(team_name + ": Invalid School")
                continue
            team_school = School.objects.get(name__iexact=school_name)

        hybrid_school_name = sheet.cell(i, 2).value.strip()
        hybrid_school = None
        if hybrid_school_name != "":
            try:
                hybrid_school = School.objects.get(name__iexact=hybrid_school_name)
            except Exception:
                # Create school through SchoolForm because for some reason they don't
                save otherwise
                form = SchoolForm(data={"name": hybrid_school_name})
                if form.is_valid():
                    form.save()
                    hybrid_school = School.objects.get(name__iexact=hybrid_school_name)
                else:
                    team_errors.append(team_name + ": Invalid Hybrid School")
                    continue

        team_seed = sheet.cell(i, 3).value.strip().lower()
        if team_seed == "full seed" or team_seed == "full":
            team_seed = 3
        elif team_seed == "half seed" or team_seed == "half":
            team_seed = 2
        elif team_seed == "free seed" or team_seed == "free":
            team_seed = 1
        elif (
            team_seed == "unseeded"
            or team_seed == "un"
            or team_seed == "none"
            or team_seed == ""
        ):
            team_seed = 0
        else:
            team_errors.append(team_name + ": Invalid Seed Value")
            continue

        deb1_name = sheet.cell(i, 4).value
        if deb1_name == "":
            team_errors.append(team_name + ": Empty Debater-1 Name")
            continue
        try:
            Debater.objects.get(name=deb1_name)
            team_errors.append(team_name + ": Duplicate Debater-1 Name")
            continue
        except Exception:
            pass
        deb1_status = sheet.cell(i, 5).value.lower()
        if deb1_status == "novice" or deb1_status == "nov" or deb1_status == "n":
            deb1_status = 1
        else:
            deb1_status = 0

        iron_man = False
        deb2_name = sheet.cell(i, 6).value

        if deb2_name == "":
            iron_man = True
        if not iron_man:
            try:
                Debater.objects.get(name=deb2_name)
                team_errors.append(team_name + ": Duplicate Debater-2 Name")
                continue
            except Exception:
                pass
            deb2_status = sheet.cell(i, 7).value.lower()

            if deb2_status == "novice" or deb2_status == "nov" or deb2_status == "n":
                deb2_status = 1
            else:
                deb2_status = 0

        # Save Everything
        try:
            deb1 = Debater(name=deb1_name, novice_status=deb1_status)
            deb1.save()
        except Exception:
            team_errors.append(team_name + ": Unkown Error Saving Debater 1")
            continue
        if not iron_man:
            try:
                deb2 = Debater(name=deb2_name, novice_status=deb2_status)
                deb2.save()
            except Exception:
                team_errors.append(team_name + ": Unkown Error Saving Debater 2")
                team_errors.append(
                    "        WARNING: Debaters on this team may be added to database. "
                    + "Please Check this Manually"
                )
                continue

        team = Team(
            name=team_name,
            school=team_school,
            hybrid_school=hybrid_school,
            seed=team_seed,
        )

        try:
            team.save()
            team.debaters.add(deb1)
            if not iron_man:
                team.debaters.add(deb2)
            else:
                team_errors.append(
                    team_name + ": Detected to be Iron Man - Still added successfully"
                )
            team.save()
        except Exception:
            team_errors.append(team_name + ": Unknown Error Saving Team")
            team_errors.append(
                "        WARNING: Debaters on this team may be added to database. "
                + "Please Check this Manually"
            )

    return team_errors
