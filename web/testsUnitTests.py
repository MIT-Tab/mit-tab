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

#Testing Code. Run Master Test to ensure everything working
#Need to add assertions to full_tourney, odd, and iron man to verify varsity/novice break, and varsity/novice speaks


import settings
from tab.models import *
import random
from decimal import *
import tab_logic
import errors
import time
import xlrd
#from gen_assertions_to_test import create_assertions
from testHelperMethods import *
import random



#Tests for tab_logic.ready_to_pair method
def all_ready_to_pair_tests():
    passed = 0
    failed = 0
    if test_not_enough_judges() == True:
        passed +=1
    else:
        failed +=1
        
    if test_not_enough_rooms() == True:
        passed +=1
    else:
        failed +=1
    if test_no_victor_entered() == True:
        passed +=1
    else:
        failed +=1
    if test_roundStats_object_missing() == True:
        passed +=1
    else:
        failed +=1
    if test_is_ready_to_pair() == True:
        passed +=1
    else:
        failed +=1

    if failed == 0:
        return "ALL TESTS PASSED"
    else:
        return passed +  " out of " + passed+failed + " tests passed."
    
#Check that it fails throws the correct assertion and fails if there aren't enough judges
#Make sure an error gets thrown if there aren't enough judges in the round
def test_not_enough_judges():
    create_tourney_judge_separate_school(10)
    judges = Judge.objects.all()
    Judge.delete(judges[0])
    threwException = False
    try:
        tab_logic.ready_to_pair(1)
    except errors.NotEnoughJudgesError:
        threwException = True
    try:
        assert threwException == True
    except AssertionError:
        print "test_not_enough_judges FAILED"
        return False
    return True

        
#Check that it fails throws the correct assertion and fails if there aren't enough rooms
def test_not_enough_rooms():
    create_tourney_judge_separate_school(10)
    rooms = Room.objects.all()
    Room.delete(rooms[0])
    threwException = False
    try:
        tab_logic.ready_to_pair(1)
    except errors.NotEnoughRoomsError:
        threwException = True
    try:
        assert threwException == True
    except AssertionError:
        print "test_not_enough_rooms FAILED"
        return False
    return True
        

#Check that it fails if all results from the previous round are not entered
def test_no_victor_entered():
    create_tourney_judge_separate_school(10)
    tab_logic.pair_round()
    enter_results(1)
    add_check_in(2)
    r = Round.objects.filter(round_number = 1)[0]
    r.victor = 0
    r.save()
    threwException = False
    try:
        tab_logic.ready_to_pair(2)
    except errors.PrevRoundNotEnteredError:
        threwException = True
    try:
        assert threwException == True
    except AssertionError:
        print "test_no_victor_entered FAILED"
        return False
    return True

def test_roundStats_object_missing():
    create_tourney_judge_separate_school(10)
    tab_logic.pair_round()
    enter_results(1)
    add_check_in(2)
    rs = RoundStats.delete(RoundStats.objects.all()[0])
    threwException = False
    try:
        tab_logic.ready_to_pair(2)
    except errors.PrevRoundNotEnteredError:
        threwException = True
    try:
        assert threwException == True
    except AssertionError:
        print "test_roundStats_object_missing FAILED"
        return False
    return True
    


#Check that it passes if all conditions are meet
def test_is_ready_to_pair():
    create_tourney_judge_separate_school(10)
    try:
        tab_logic.ready_to_pair(1)
    except:
        print "test_is_ready_to_pair FAILED"
        return False
    return True


