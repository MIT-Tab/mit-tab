# Copyright (C) 2011 by Julia Boortz and Joseph Lynch

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

from decimal import *
import xlrd

from mittab.apps.tab.forms import JudgeForm
from mittab.apps.tab.models import *


def import_judges(import_file, using_overwrite=False):
    try:
        sh = xlrd.open_workbook(filename=None, file_contents=import_file.read()).sheet_by_index(0)
    except:
        return ["ERROR: Please upload an .xls file. This file type is not compatible"]

    judge_entries = 0
    found_end = False

    errors = []
    while not found_end:
        try:
            sh.cell(judge_entries, 0).value  # find file end
            judge_entries += 1
        except IndexError:
            found_end = True

        # Verify sheet has required number of columns
        try:
            sh.cell(0, 1).value
        except IndexError:
            errors.append("ERROR: Insufficient Columns in sheet. No Data Read")
            return errors

    for i in range(1, judge_entries):

        # 0     name
        # 1     rank
        # 2     phone
        # 3     provider
        # 4+    schools

        is_duplicate = False

        # Load and validate Judge's Name
        judge_name = sh.cell(i, 0).value
        if Judge.objects.filter(name=judge_name).exists():
            errors.append(judge_name + ': duplicate judge name, skipping')
            is_duplicate = True

        # Load and validate judge_rank
        judge_rank = sh.cell(i, 1).value
        try:
            judge_rank = Decimal(judge_rank)
        except TypeError or ValueError:
            errors.append(judge_name + ": Rank is not a number")
            continue

        if judge_rank > 100 or judge_rank < 0:
            errors.append(judge_name + ": Rank should be between 0-100")
            continue

        judge_phone = sh.cell(i, 2).value
        judge_provider = sh.cell(i, 3).value

        # iterate through schools until none are left
        cur_col = 4
        schools = []
        while True:
            try:
                judge_school = sh.cell(i, cur_col).value
                # If other judges have more schools but this judge doesn't, we get an empty string
                # If blank, keep iterating in case user has a random blank column for some reason
                if judge_school != '':
                    try:
                        # Get id from the name because JudgeForm requires we use id
                        s = School.objects.get(name=judge_school).id
                        schools.append(s)
                    except IndexError:
                        break
                    except:
                        try:
                            s = School(name=judge_school)
                            s.save()
                            schools.append(s.id)
                        except:
                            errors.append(judge_name + ': Invalid School')
                            continue
            except IndexError:
                break
            cur_col += 1

        if not is_duplicate:
            form = JudgeForm(data={'name': judge_name, 'rank': judge_rank, 'phone': judge_phone,
                                   'provider': judge_provider, 'schools': schools})
            if form.is_valid():
                form.save()
            else:
                print form.errors  # print errors of console so they can actually be debugged
                errors.append(judge_name + ": Form invalid. Check inputs.")

        else:
            if using_overwrite:
                # overwrite the parameters for that judge if using overwrite
                judge = Judge.objects.get(name=judge_name)
                judge.rank = judge_rank
                judge.phone = judge_phone
                judge.provider = judge_provider
                judge.school = schools
                judge.save()

            else:
                # do nothing
                pass

        return errors
