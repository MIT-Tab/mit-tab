from mittab.apps.tab.models import *
import xlrd
from xlwt import Workbook


def import_scratches(fileToImport):
    sh = xlrd.open_workbook(fileToImport).sheet_by_index(0)
    num_scratches = 0
    found_end = False
    scratch_errors = []
    while found_end == False:
        try:
            sh.cell(num_scratches, 0).value
            num_scratches += 1
        except IndexError:
            found_end = True
    for i in range(1, num_scratches):
        try:
            team_name = sh.cell(i, 0).value
            t = Team.objects.get(name=team_name)
            judge_name = sh.cell(i, 1).value
            j = Judge.objects.get(name=judge_name)
            s_type = sh.cell(i, 2).value.lower()
            if s_type == "team scratch" or s_type == "team":
                s_type = 0
            elif s_type == "tab scratch" or s_type == "tab":
                s_type = 1
            s = Scratch(judge=j, team=t, scratch_type=s_type)
            try:
                s.save()
            except:
                scratch_errors.append[[j, t]]
        except Exception as e:
            print(e)
    return scratch_errors
