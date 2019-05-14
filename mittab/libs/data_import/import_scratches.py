import xlrd

from mittab.apps.tab.models import Scratch, Team, Judge


def import_scratches(file_to_import):
    sheet = xlrd.open_workbook(file_to_import).sheet_by_index(0)
    num_scratches = 0
    found_end = False
    scratch_errors = []
    while not found_end:
        try:
            sheet.cell(num_scratches, 0).value
            num_scratches += 1
        except IndexError:
            found_end = True
    for i in range(1, num_scratches):
        try:
            team_name = sheet.cell(i, 0).value
            team = Team.objects.get(name=team_name)
            judge_name = sheet.cell(i, 1).value
            judge = Judge.objects.get(name=judge_name)
            s_type = sheet.cell(i, 2).value.lower()
            if s_type == "team scratch" or s_type == "team":
                s_type = 0
            elif s_type == "tab scratch" or s_type == "tab":
                s_type = 1
            s = Scratch(judge=judge, team=team, scratch_type=s_type)
            try:
                s.save()
            except Exception:
                msg = "Error creating scratch on %s from %s (row %d)" % (judge, team, i)
                scratch_errors.append(msg)
        except Exception as e:
            print(e)
    return scratch_errors
