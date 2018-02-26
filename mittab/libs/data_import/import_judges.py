#Copyright (C) 2011 by Julia Boortz and Joseph Lynch

#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:

#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.

#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.

from mittab.apps.tab.models import *
from mittab.apps.tab.forms import JudgeForm

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
    while found_end == False:
        try:
            sh.cell(num_judges, 0).value
            num_judges += 1
        except IndexError:
            found_end = True

        #Verify sheet has required number of columns
        try:
            sh.cell(0, 1).value
        except:
            team_errors.append("ERROR: Insufficient Columns in sheet. No Data Read")
            return team_errors
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

        #iterate through schools until none are left
        cur_col = 2
        schools = []
        while(True):
            try:
                judge_school = sh.cell(i, cur_col).value
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
            except IndexError:
                break
            cur_col += 1

        data = {'name': judge_name, 'rank': judge_rank, 'schools': schools}
        form = JudgeForm(data=data)
        if (form.is_valid()):
            form.save()
        else:
            error_messages = sum([ error[1] for error in form.errors.items() ], [])
            error_string = ', '.join(error_messages)
            judge_errors.append("%s: %s" % (judge_name, error_string))

    return judge_errors

