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

from tab.models import *
import xlrd
import csv
from xlwt import Workbook

def import_judges(fileToImport):
    wb = xlrd.open_workbook(fileToImport)
    sh = wb.sheet_by_index(0)
    num_judges = 0
    found_end = False
    while found_end == False:
        try:
            sh.cell(num_judges,0).value
            num_judges +=1
        except IndexError:
            found_end = True
    for i in range(num_judges):
        if i != 0:
            judge_school = sh.cell(i,1).value
            try:
                s = School(name = judge_school)
                s.save()
            except:
                s = School.objects.get(name = judge_school)
                print s
            finally:
                judge_name = sh.cell(i,0).value
                judge_rank = sh.cell(i,2).value
                judge_phone = sh.cell(i,3).value
                judge_provider = sh.cell(i,4).value
                print s
                j = Judge(name = judge_name, school = s, rank = judge_rank, phone = judge_phone, provider = judge_provider)
                try:
                    j.save()
                    print "Added %s" % judge_name
                except:
                    print "Could not save %s" % judge_name
                
    print "judges entered"
        
