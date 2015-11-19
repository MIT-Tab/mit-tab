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
import xlrd
import csv
from xlwt import Workbook

def import_scratches(fileToImport):
    sh = xlrd.open_workbook(fileToImport).sheet_by_index(0)
    num_scratches = 0
    found_end = False
    scratch_errors = []
    while found_end == False:
        try:
            sh.cell(num_scratches,0).value
            num_scratches +=1
        except IndexError:
            found_end = True
    for i in range(1, num_scratches):
        try:
            team_name = sh.cell(i,0).value
            t = Team.objects.get(name = team_name)
            judge_name = sh.cell(i,1).value
            j = Judge.objects.get(name = judge_name)
            s_type = sh.cell(i,2).value.lower()
            if s_type == "team scratch" or s_type == "team":
                s_type = 0
            elif s_type == "tab scratch" or s_type == "tab":
                s_type = 1
            s = Scratch(judge = j, team = t, scratch_type = s_type)
            try:
                s.save()
            except:
                scratch_errors.append[[j,t]]
        except Exception as e:
            print e
    return scratch_errors
