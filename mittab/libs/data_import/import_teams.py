from mittab.apps.tab.models import *
from mittab.apps.tab.forms import SchoolForm
from mittab.libs.data_import import value_or_empty

import xlrd
from xlwt import Workbook

def import_teams(fileToImport):
    try:
        sh = xlrd.open_workbook(filename=None, file_contents=fileToImport.read()).sheet_by_index(0)
    except:
        return ['ERROR: Please upload an .xlsx file. This filetype is not compatible']
    num_teams = 0
    found_end = False
    team_errors = []

    #Verify sheet has required number of columns
    try:
        sh.cell(0, 8).value
    except:
        team_errors.append('ERROR: Insufficient Columns in Sheet. No Data Read')
        return team_errors

    while found_end == False:
        if value_or_empty(sh, num_judges, 0):
            num_judges += 1
        else:
            found_end = True

    for i in range(1, num_teams):

        team_name = sh.cell(i, 0).value
        if team_name == '':
            team_errors.append('Row ' + str(i) + ': Empty Team Name')
            continue
        try:
            Team.objects.get(name=team_name)
            team_errors.append(team_name + ': Duplicate Team Name')
            continue
        except:
            pass

        school_name = sh.cell(i, 1).value.strip()
        try:
            team_school = School.objects.get(name__iexact=school_name)
        except:
            #Create school through SchoolForm because for some reason they don't save otherwise
            form = SchoolForm(data={'name': school_name})
            if form.is_valid():
                form.save()
            else:
                team_errors.append(team_name + ": Invalid School")
                continue
            team_school = School.objects.get(name__iexact=school_name)


        #TODO: Verify there are not multiple free seeds from the same school
        team_seed = sh.cell(i,2).value.strip().lower()
        if team_seed == 'full seed' or team_seed == 'full':
            team_seed = 3
        elif team_seed == 'half seed' or team_seed == 'half':
            team_seed = 2
        elif team_seed == 'free seed' or team_seed == 'free':
            team_seed = 1
        elif team_seed == 'unseeded' or team_seed == 'un' or team_seed == 'none' or team_seed == '':
            team_seed = 0
        else:
            team_errors.append(team_name + ': Invalid Seed Value')
            continue

        deb1_name = sh.cell(i,3).value
        if deb1_name == '':
            team_errors.append(team_name + ': Empty Debater-1 Name')
            continue
        try:
            Debater.objects.get(name=deb1_name)
            team_errors.append(team_name + ': Duplicate Debater-1 Name')
            continue
        except:
            pass
        deb1_status = sh.cell(i,4).value.lower()
        if deb1_status == 'novice' or deb1_status == 'nov' or deb1_status == 'n':
            deb1_status = 1
        else:
            deb1_status = 0
        deb1_phone = sh.cell(i,5).value
        deb1_provider = sh.cell(i,6).value


        iron_man = False
        deb2_name = sh.cell(i,7).value
        if deb2_name == '':
            iron_man = True
        if (not iron_man):
            try:
                Debater.objects.get(name=deb2_name)
                team_errors.append(team_name + ': Duplicate Debater-2 Name')
                continue
            except:
                pass
            deb2_status = sh.cell(i,8).value.lower()
            if deb2_status == 'novice' or deb2_status == 'nov' or deb2_status == 'n':
                deb2_status = 1
            else:
                deb2_status = 0

            #Since this is not required data and at the end of the sheet, be ready for index errors
            try: 
                deb2_phone = sh.cell(i,9).value
            except IndexError:
                deb2_phone = ''
            try:
                deb2_provider = sh.cell(i,10).value
            except IndexError:
                deb2_provider = ''


        #Save Everything
        try:
            deb1 = Debater(name = deb1_name, novice_status = deb1_status, phone = deb1_phone, provider = deb1_provider)
            deb1.save()
        except:
            team_errors.append(team_name + ': Unkown Error Saving Debater 1')
            continue
        if (not iron_man):
            try:
                deb2 = Debater(name = deb2_name, novice_status = deb2_status, phone = deb2_phone, provider = deb2_provider)
                deb2.save()
            except:
                team_errors.append(team_name + ': Unkown Error Saving Debater 2')
                team_errors.append('        WARNING: Debaters on this team may be added to database. ' +
                                    'Please Check this Manually')
                continue
        
        team = Team(name=team_name, school=team_school, seed=team_seed)
        try:
            team.save()
            team.debaters.add(deb1)
            if (not iron_man):
                team.debaters.add(deb2)
            else:
                team_errors.append(team_name + ": Detected to be Iron Man - Still added successfully")
            team.save()
        except:
            team_errors.append(team_name + ': Unknown Error Saving Team')
            team_errors.append('        WARNING: Debaters on this team may be added to database. ' +
                                'Please Check this Manually')

    return team_errors

    

