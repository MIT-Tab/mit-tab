from mittab.apps.tab.models import *
from mittab.apps.tab.forms import JudgeForm
from mittab.libs.data_import import value_or_empty

from decimal import *
import xlrd
from xlwt import Workbook

def import_judges(fileToImport):
    try:
        sh = xlrd.open_workbook(filename=None, file_contents=fileToImport.read()).sheet_by_index(0)
    except:
        return ["ERROR: Please upload an .xlsx file. This filetype is not compatible"]
    num_judges = 0
    found_end = False
    judge_errors = []

    #Verify sheet has required number of columns
    try:
        sh.cell(0, 1).value
    except:
        team_errors.append("ERROR: Insufficient Columns in sheet. No Data Read")
        return team_errors

    # Get the number of judges
    # TODO: refactor this to a shared function
    while value_or_empty(sh, num_judges, 0):
        num_judges += 1

    for i in range(1, num_judges):
        #Load and validate Judge's Name
        judge_name = sh.cell(i, 0).value
        try:
            Judge.objects.get(name=judge_name)
            judge_errors.append(judge_name + ": Duplicate Judge Name")
            continue
        except:
            pass

        #Load and validate judge_rank
        judge_rank = sh.cell(i, 1).value
        try:
            judge_rank = round(float(judge_rank), 2)
        except:
            judge_errors.append(judge_name + ": Rank not number")
            continue
        if judge_rank > 100 or judge_rank < 0:
            judge_errors.append(judge_name + ": Rank should be between 0-100")
            continue

        #Because this data is not required, be prepared for IndexErrors
        judge_phone = value_or_empty(sh, i, 2)
        if judge_phone:
            judge_phone = str(int(judge_phone))
        judge_provider = value_or_empty(sh, i, 3)

        #iterate through schools until none are left
        cur_col = 4
        schools = []
        while(True):
            judge_school = value_or_empty(sh, i, cur_col)
            #If other judges have more schools but this judge doesn't, we get an empty string
            #If blank, keep iterating in case user has a random blank column for some reason
            if (judge_school != ''):
                try:
                    #Get id from the name because JudgeForm requires we use id
                    s = School.objects.get(name__iexact=judge_school).id
                    schools.append(s)
                except IndexError:
                    break
                except:
                    try:
                        s = School(name=judge_school)
                        s.save()
                        schools.append(s.id)
                    except:
                        judge_errors.append(judge_name + ': Invalid School')
                        continue
            cur_col += 1

        data = {'name': judge_name, 'rank': judge_rank, 'phone': judge_phone, 'provider': judge_provider, 'schools': schools}
        form = JudgeForm(data=data)
        if (form.is_valid()):
            form.save()
        else:
            error_messages = sum([ error[1] for error in form.errors.items() ], [])
            error_string = ', '.join(error_messages)
            judge_errors.append("%s: %s" % (judge_name, error_string))

    return judge_errors

