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
from mittab.apps.tab.forms import TeamForm

import xlrd
from xlwt import Workbook

def import_teams(fileToImport):
    try:
        sh = xlrd.open_workbook(filename=None, file_contents=fileToImport.read()).sheet_by_index(0)
    except:
        return ["ERROR: Please upload an .xlsx file. This filetype is not compatible"]
    num_teams = 0
    found_end = False
    team_errors = []
    while found_end == False:
        try:
            sh.cell(num_teams,0).value
            num_teams +=1
        except IndexError:
            found_end = True

    for i in range(1, num_teams):

        team_name = sh.cell(i,0).value
        if team_name == '':
            team_errors.append("Row " + str(i) + ": Empty Team Name")
            continue
        try:
            theName = Team.objects.get(name=team_name)
            team_errors.append(team_name + ": Duplicate Team Name")
            continue
        except:
            pass

        team_school = sh.cell(i,1).value
        print team_school
        try:
            team_school = School.objects.get(name=team_school)
        except:
            team_errors.append(team_name + ": Invalid School")
            continue
        

        #TODO: Verify there are not multiple free seeds from the same school
        team_seed = sh.cell(i,2).value.lower()
        if team_seed == "full seed" or team_seed == "full":
            team_seed = 3
        elif team_seed == "half seed" or team_seed == "half":
            team_seed = 2
        elif team_seed == "free seed" or team_seed == "free":
            team_seed = 1
        elif team_seed == "unseeded" or team_seed == "un" or team_seed == "none" or team_seed == '':
            team_seed = 0
        else:
            team_errors.append(team_name + ": Invalid Seed Value")
            continue

        deb1_name = sh.cell(i,3).value
        if deb1_name == '':
            team_errors.append(team_name + ": Empty Debater-1 Name")
            continue
        try:
            theName = Debater.objects.get(name=deb1_name)
            team_errors.append(team_name + ": Duplicate Debater-1 Name")
            continue
        except:
            pass
        deb1_status = sh.cell(i,4).value.lower()
        if deb1_status == "novice" or deb1_status == "nov" or deb1_status == "n":
            deb1_status = 1
        else:
            deb1_status = 0
        deb1_phone = sh.cell(i,5).value
        deb1_provider = sh.cell(i,6).value

        deb2_name = sh.cell(i,7).value
        if deb2_name == '':
            team_errors.append(team_name + ": Empty Debater-2 Name")
            continue
        try:
            theName = Debater.objects.get(name=deb2_name)
            team_errors.append(team_name + ": Duplicate Debater-2 Name")
            continue
        except:
            pass
        deb2_status = sh.cell(i,8).value.lower()
        if deb2_status == "novice" or deb2_status == "nov" or deb2_status == "n":
            deb2_status = 1
        else:
            deb2_status = 0
        deb2_phone = sh.cell(i,9).value
        deb2_provider = sh.cell(i,10).value

        deb1 = Debater(name = deb1_name, novice_status = deb1_status, phone = deb1_phone, provider = deb1_provider)
        deb1.save()
        deb2 = Debater(name = deb2_name, novice_status = deb2_status, phone = deb2_phone, provider = deb2_provider)
        deb2.save()

        team = Team(name = team_name, school = team_school, seed = team_seed)
        try:
            team.save()
            team.debaters.add(deb1)
            team.debaters.add(deb2)
            team.save()
        except:
            team_errors.append(team_name)

    return team_errors

    

