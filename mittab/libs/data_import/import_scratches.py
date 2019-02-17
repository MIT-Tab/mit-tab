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
from __future__ import print_function
import pandas as pd
from django.db import IntegrityError

from xlrd import XLRDError

from mittab.apps.tab.models import *


def _verify_scratch_type(s_type):
    # TYPE_CHOICES = (
    #     (TEAM_SCRATCH, u'Team Scratch'),
    #     (TAB_SCRATCH, u'Tab Scratch'),
    # )
    try:
        # if input is substring at start of lower-cased description (i.e. 'team'), match
        return next(i for i, description in Scratch.TYPE_CHOICES if description.lower().startswith(s_type.lower()))
    except AttributeError:
        # attribute error raised if type(s_type) != str
        # check whether inputs contained in type choices' integer codes
        return next(i for i, description in Scratch.TYPE_CHOICES if i == s_type)


def import_scratches(import_file):
    try:  # try to read as excel
        scratch_df = pd.read_excel(import_file)
    except XLRDError:  # if not excel, try as CSV
        scratch_df = pd.read_csv(import_file)

    scratch_errors = []
    required_columns = {'team_name', 'judge_name', 'scratch_type'}

    if set(scratch_df.columns.values.tolist()) != required_columns:
        # checks whether the required columns are contained, if false, then...
        scratch_errors.append('missing columns, needed columns are {}'.format(required_columns))
        return

    for i, row in scratch_df.iterrows():
        team_name = row['team_name']
        judge_name = row['judge_name']
        scratch_type = row['scratch_type']

        try:  # clean the scratch type
            clean_stype = _verify_scratch_type(scratch_type)
        except StopIteration:
            scratch_errors.append('error on line {}, scratch type {} is not valid code, skip'.format(i, scratch_type))
            continue

        except Exception as e:
            scratch_errors.append('error on line {} unknown non-conforming problem, skip'.format(i, scratch_type))
            print(e)
            continue

        try:  # try to find the team
            team = Team.objects.get(name=team_name)
        except Team.DoesNotExist:
            scratch_errors.append('could not find team with name {}. skipped'.format(team_name))
            continue

        try:  # try to find the judge
            judge = Judge.objects.get(name=judge_name)
        except Judge.DoesNotExist:
            scratch_errors.append('could not find judge with name {}. skipped'.format(judge_name))
            continue

        try:  # try to save the scratch
            scratch = Scratch(team=team, judge=judge, scratch_type=clean_stype)
            scratch.save()
            print('saved scratch on {} by {}'.format(judge.name, team.name))

        except IntegrityError:
            # duplicated? skip
            scratch_errors.append('could not save scratch on {} by team {},'
                                  ' exists. skipped'.format(judge_name, team_name))
            continue

        except Exception as e:
            scratch_errors.append('could not save scratch on {} by team {}, unknown err'.format(judge_name, team_name))
            print(e)
            continue

    # return errors
    return scratch_errors
