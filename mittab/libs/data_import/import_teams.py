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

import collections
import xlrd
from django.core.exceptions import ObjectDoesNotExist

from mittab.apps.tab.forms import SchoolForm
from mittab.apps.tab.models import *


def import_teams(fileToImport):
    try:
        sh = xlrd.open_workbook(filename=None, file_contents=fileToImport.read()).sheet_by_index(0)
    except:
        return ['ERROR: Please upload an .xlsx file. This filetype is not compatible']
    num_teams = 0
    found_end = False
    team_errors = []
    while found_end == False:
        try:
            sh.cell(num_teams, 0).value
            num_teams +=1
        except IndexError:
            found_end = True

        #Verify sheet has required number of columns
        try:
            sh.cell(0, 8).value
        except:
            team_errors.append('ERROR: Insufficient Columns in Sheet. No Data Read')
            return team_errors

    # verify no duplicate debaters, give error messages
    deb_indicies = []
    for i in range(1, num_teams):
        deb_indicies.append((sh.cell(i, 3).value.strip(), i))  # tuple saves debater name and row
        deb_indicies.append((sh.cell(i, 7).value.strip(), i))
    deb_names = [i[0] for i in deb_indicies]
    names_dict = collections.Counter(deb_names)
    for deb_index in deb_indicies:
        if names_dict.get(deb_index[0]) > 1:  # if dict says appears more than once
            # inform that duplicate exists at location, report relevant information
            row_num = deb_index[1]
            msg = "Check for duplicate debater " + deb_index[0] + " in team " + sh.cell(row_num, 0).value + \
                  ", on XLS file row " + str(row_num)
            team_errors.append(msg)

    for i in range(1, num_teams):

        # Name, School, Seed [full, half, free, none], D1 name, D1 v/n?, D1 phone, D1 prov,
        # D2 name, D2 v/n?, D2 phone, D2 prov

        # team name, check for duplicates
        duplicate = False
        team_name = sh.cell(i, 0).value
        if team_name == '':
            team_errors.append('Skipped row ' + str(i) + ': empty Team Name')
            continue
        if Team.objects.filter(name=team_name).first() is not None:  # inform that duplicates exist
            duplicate = True
            team_errors.append(team_name + ': duplicate team, overwriting data')

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

        # check seeds
        team_seed, changed_seed = _create_seed(team_name=team_name, seed=sh.cell(i, 2).value.strip().lower())
        if changed_seed:
            team_errors.append('Changed ' + team_name + ' from "' + sh.cell(i, 2).value.strip().lower()
                               + '" to unseeded. Note and confirm with school.')

        deb1_name = sh.cell(i, 3).value.strip()
        deb1_status = _create_status(sh.cell(i, 4).value.lower())
        deb1_phone = sh.cell(i, 5).value.strip()
        deb1_provider = sh.cell(i, 6).value.strip()
        deb1, deb1_created = Debater.objects.get_or_create(name=deb1_name, novice_status=deb1_status, phone=deb1_phone,
                                                           provider=deb1_provider)

        iron_man = True
        deb2_name = sh.cell(i, 7).value.strip()
        if deb2_name is not '':
            iron_man = False
            deb2_status = _create_status(sh.cell(i, 8).value.lower())
            try:
                deb2_phone = sh.cell(i, 9).value
            except IndexError:
                deb2_phone = ''
            try:
                deb2_provider = sh.cell(i,10).value
            except IndexError:
                deb2_provider = ''
            deb2, deb2_created = Debater.objects.get_or_create(name=deb2_name, novice_status=deb2_status,
                                                               phone=deb2_phone,
                                                               provider=deb2_provider)

        if not duplicate:  # create new team
            team = Team(name=team_name, school=team_school, seed=team_seed)
            team.save()
            team.debaters.add(deb1)
            if not iron_man:
                team.debaters.add(deb2)
            else:
                team_errors.append(team_name + ': Team is an iron-man, added successfully')
            team.save()

        else:  # update the team
            team = Team.objects.get(name=team_name)
            team.school = team_school
            team.seed = team_seed

            team.debaters.clear()
            team.debaters.add(deb1)
            if not iron_man:
                team.debaters.add(deb2)
            else:
                team_errors.append(team_name + ': Team is an iron-man, added successfully')
            team.save()

    return team_errors


def _create_status(status):
    """Translates the string for varsity-novice status into MIT-TAB's integer pseudo-enum"""
    if status == 'novice' or status == 'nov' or status == 'n':
        return 1
    else:
        return 0


def _create_seed(team_name, seed):
    """Translates the string version of the seed into the pseudo-enum. Checks for duplicate free seeds and changes it
    as necessary. Also notes that change so a message can be returned.
    :type team_name: str
    :type seed: str
    :return tuple with the integer version of the seed and whether that team's seed was changed
    """
    seed_int = 0
    seed_changed = False

    if seed == 'full seed' or seed == 'full':
        seed_int = 3
    elif seed == 'half seed' or seed == 'half':
        seed_int = 2
    elif seed == 'free seed' or seed == 'free':
        seed_int = 1

        multiple_free_seeds = False
        try:
            school_name = Team.objects.get(name=team_name).school  # get school_name
            for team in Team.objects.filter(school=school_name).all():  # get teams with that name
                if int(team.seed) == 1:  # 1 is the free seed
                    if team.name != team_name:  # if there is a free seed already, change and note change
                        multiple_free_seeds = True

        except ObjectDoesNotExist:
            pass

        if multiple_free_seeds:  # force free, note this
            seed_changed = True
            seed_int = 0

    return seed_int, seed_changed