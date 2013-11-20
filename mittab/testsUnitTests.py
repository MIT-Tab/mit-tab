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


#########################################
#Tests for tab_logic.ready_to_pair method
#########################################
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
        return str(passed) +  " out of " + str(passed+failed) + " tests passed."
    
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


#############################################################
#Tests for tab_logic.add_scratches_for_school_affil() method
#############################################################

def all_add_scratches_for_school_affil_tests():
    passed = 0
    failed = 0
    if test_judges_from_another_school() == True:
        passed +=1
    else:
        failed +=1

    if test_nothing_to_add_tab_scratch() == True:
        passed +=1
    else:
        failed +=1

    if test_nothing_to_add_team_scratch() == True:
        passed +=1
    else:
        failed +=1

    if test_add_one() == True:
        passed +=1
    else:
        failed +=1

    if test_add_multi_team() == True:
        passed +=1
    else:
        failed +=1

    if test_add_multi_judge() == True:
        passed +=1
    else:
        failed +=1

    if test_many_adds() == True:
        passed +=1
    else:
        failed +=1


    if failed == 0:
        return "ALL TESTS PASSED"
    else:
        return str(passed) +  " out of " + str(passed+failed) + " tests passed."

#Verify that works if the judges are from a different school than all debaters
def test_judges_from_another_school():
    create_tourney_judge_separate_school(10)
    try:
        tab_logic.add_scratches_for_school_affil()
    except:
        print "test_judges_from_another_school FAILED"
        return False
    if Scratch.objects.all().count() == 0:
        return True
    else:
        print "test_judges_from_another_school FAILED"
        return False


#Verify that works if scratch was already added as tab scratch from judge side
def test_nothing_to_add_tab_scratch():
    clear_db()
    add_round_stats(1,5,4,2)
    d1 = Debater.objects.create(name = "deb1", novice_status = 0)
    d2 = Debater.objects.create(name = "deb2", novice_status = 0)
    sch = School.objects.create(name = "school1")
    j = Judge.objects.create(name = "judge1", rank = 1, school = sch)
    t = Team.objects.create(name = "team1", school = sch, seed = 0)
    s = Scratch.objects.create(judge = j, team = t, scratch_type = 1)
    try:
        tab_logic.add_scratches_for_school_affil()
    except:
        print "test_nothing_to_add_tab_scratch FAILED"
        return False
    if Scratch.objects.all()[0] == s:
        return True
    else:
        print "test_nothing_to_add_tab_scratch FAILED"
        return False


#Verify that works if scratch was already added as team scratch from judge side
def test_nothing_to_add_team_scratch():
    clear_db()
    add_round_stats(1,5,4,2)
    d1 = Debater.objects.create(name = "deb1", novice_status = 0)
    d2 = Debater.objects.create(name = "deb2", novice_status = 0)
    sch = School.objects.create(name = "school1")
    j = Judge.objects.create(name = "judge1", rank = 1, school = sch)
    t = Team.objects.create(name = "team1", school = sch, seed = 0)
    s = Scratch.objects.create(judge = j, team = t, scratch_type = 0)
    try:
        tab_logic.add_scratches_for_school_affil()
    except:
        print "test_nothing_to_add_team_scratch FAILED"
        return False
    if Scratch.objects.all()[0] == s:
        return True
    else:
        print "test_nothing_to_add_team_scratch FAILED"
        return False


#Verify that works if need to add one scratch
def test_add_one():
    clear_db()
    add_round_stats(1,5,4,2)
    d1 = Debater.objects.create(name = "deb1", novice_status = 0)
    d2 = Debater.objects.create(name = "deb2", novice_status = 0)
    s = School.objects.create(name = "school1")
    j = Judge.objects.create(name = "judge1", rank = 1, school = s)
    t = Team.objects.create(name = "team1", school = s, seed = 0)
    try:
        tab_logic.add_scratches_for_school_affil()
    except:
        print "test_add_one FAILED"
        return False
    if len(Scratch.objects.all()) != 1:
        print "test_add_one FAILED"
        return False
    s = Scratch.objects.all()[0]
    if s.team != t or s.judge != j or s.scratch_type != 1:
        print "test_add_one FAILED"
        return False
    return True

#Verify that works if need to add more than one scratch for a specific team
def test_add_multi_team():
    clear_db()
    add_round_stats(1,5,4,2)
    d1 = Debater.objects.create(name = "deb1", novice_status = 0)
    d2 = Debater.objects.create(name = "deb2", novice_status = 0)
    sch = School.objects.create(name = "school1")
    j1 = Judge.objects.create(name = "judge1", rank = 1, school = sch)
    j2 = Judge.objects.create(name = "judge2", rank = 1, school = sch)
    t = Team.objects.create(name = "team1", school = sch, seed = 0)
    try:
        tab_logic.add_scratches_for_school_affil()
    except:
        print "test_add_one FAILED"
        return False
    if len(Scratch.objects.all()) != 2:
        print "test_add_one FAILED"
        return False
    s1 = Scratch.objects.all()[0]
    if s1.team != t or s1.judge != j1 or s1.scratch_type != 1:
        print "test_add_one FAILED"
        return False
    s2 = Scratch.objects.all()[1]
    if s2.team != t or s2.judge != j2 or s2.scratch_type != 1:
        print "test_add_one FAILED"
        return False
    return True

#Verify that works if need to add more than one scratch for a specific team
def test_add_multi_judge():
    clear_db()
    add_round_stats(1,5,4,2)
    d1 = Debater.objects.create(name = "deb1", novice_status = 0)
    d2 = Debater.objects.create(name = "deb2", novice_status = 0)
    d3 = Debater.objects.create(name = "deb3", novice_status = 0)
    d4 = Debater.objects.create(name = "deb4", novice_status = 0)
    sch = School.objects.create(name = "school1")
    j = Judge.objects.create(name = "judge1", rank = 1, school = sch)
    t1 = Team.objects.create(name = "team1", school = sch, seed = 0)
    t2 = Team.objects.create(name = "team2", school = sch, seed = 0)
    try:
        tab_logic.add_scratches_for_school_affil()
    except:
        print "test_add_one FAILED"
        return False
    if len(Scratch.objects.all()) != 2:
        print "test_add_one FAILED"
        return False
    s1 = Scratch.objects.all()[0]
    if s1.team != t1 or s1.judge != j or s1.scratch_type != 1:
        print "test_add_one FAILED"
        return False
    s2 = Scratch.objects.all()[1]
    if s2.team != t2 or s2.judge != j or s2.scratch_type != 1:
        print "test_add_one FAILED"
        return False
    return True

#Verify works if need to add multiple scratches, but one was already added
def test_many_adds():
    clear_db()
    add_round_stats(1,5,4,2)
    d1 = Debater.objects.create(name = "deb1", novice_status = 0)
    d2 = Debater.objects.create(name = "deb2", novice_status = 0)
    d3 = Debater.objects.create(name = "deb3", novice_status = 0)
    d4 = Debater.objects.create(name = "deb4", novice_status = 0)
    sch = School.objects.create(name = "school1")
    j1 = Judge.objects.create(name = "judge1", rank = 1, school = sch)
    j2 = Judge.objects.create(name = "judge2", rank = 1, school = sch)
    t1 = Team.objects.create(name = "team1", school = sch, seed = 0)
    t2 = Team.objects.create(name = "team2", school = sch, seed = 0)
    scratch1 = Scratch.objects.create(judge = j1, team = t1, scratch_type = 0)
    try:
        tab_logic.add_scratches_for_school_affil()
    except:
        print "test_add_one FAILED 1"
        return False

    if len(Scratch.objects.all()) != 4:
        print "test_add_one FAILED 2"
        return False
    
    if scratch1.team != t1 or scratch1.judge != j1 or scratch1.scratch_type != 0:
        print "test_add_one FAILED 3"
        return False
    
    scratch2 = Scratch.objects.all()[1]
    if scratch2.team != t2 or scratch2.judge != j1 or scratch2.scratch_type != 1:
        print "test_add_one FAILED 4"
        return False
    return True

    scratch3 = Scratch.objects.all()[2]
    if scratch2.team != t1 or scratch3.judge != j2 or scratch3.scratch_type != 1:
        print "test_add_one FAILED 5"
        return False

    scratch4 = Scratch.objects.all()[3]
    if scratch4.team != t2 or scratch4.judge != j2 or scratch2.scratch_type != 1:
        print "test_add_one FAILED 6"
        return False

    
    return True


#############################################################
#Tests for tab_logic.highest_seed method
#############################################################
    
def all_highest_seed():
    passed = 0
    failed = 0
    if test_t1_higher_t2() == True:
        passed +=1
    else:
        failed +=1

    if test_t2_higher_t1() == True:
        passed +=1
    else:
        failed +=1

    if test_t1_equals_t2() == True:
        passed +=1
    else:
        failed +=1

    if failed == 0:
        return "ALL TESTS PASSED"
    else:
        return str(passed) +  " out of " + str(passed+failed) + " tests passed."

def test_t1_higher_t2():
    clear_db()
    add_round_stats(1,5,4,2)
    d1 = Debater.objects.create(name = "deb1", novice_status = 0)
    d2 = Debater.objects.create(name = "deb2", novice_status = 0)
    d3 = Debater.objects.create(name = "deb3", novice_status = 0)
    d4 = Debater.objects.create(name = "deb4", novice_status = 0)
    sch = School.objects.create(name = "school1")
    t1 = Team.objects.create(name = "team1", school = sch, seed = 1)
    t2 = Team.objects.create(name = "team2", school = sch, seed = 0)
    try:
        result = tab_logic.highest_seed(t1, t2)
    except:
        print "test_t1_higher_t2 FAILED"
        return False

    if result != 1:
        print "test_t1_higher_t2 FAILED"
        return False

    return True

def test_t2_higher_t1():
    clear_db()
    add_round_stats(1,5,4,2)
    d1 = Debater.objects.create(name = "deb1", novice_status = 0)
    d2 = Debater.objects.create(name = "deb2", novice_status = 0)
    d3 = Debater.objects.create(name = "deb3", novice_status = 0)
    d4 = Debater.objects.create(name = "deb4", novice_status = 0)
    sch = School.objects.create(name = "school1")
    t1 = Team.objects.create(name = "team1", school = sch, seed = 2)
    t2 = Team.objects.create(name = "team2", school = sch, seed = 3)
    try:
        result = tab_logic.highest_seed(t1, t2)
    except:
        print "test_t2_higher_t1 FAILED"
        return False
    if result != 3:
        print "test_t2_higher_t1 FAILED"
        return False
    
    return True
    

def test_t1_equals_t2():
    clear_db()
    add_round_stats(1,5,4,2)
    d1 = Debater.objects.create(name = "deb1", novice_status = 0)
    d2 = Debater.objects.create(name = "deb2", novice_status = 0)
    d3 = Debater.objects.create(name = "deb3", novice_status = 0)
    d4 = Debater.objects.create(name = "deb4", novice_status = 0)
    sch = School.objects.create(name = "school1")
    t1 = Team.objects.create(name = "team1", school = sch, seed = 3)
    t2 = Team.objects.create(name = "team2", school = sch, seed = 3)
    try:
        result = tab_logic.highest_seed(t1, t2)
    except:
        print "test_t1_equals_t2 FAILED"
        return False

    if result != 3:
        print "test_t1_equals_t2 FAILED"
        return False

    return True



