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
from gen_assertions_to_test import create_assertions


def create_tourney(num_teams):
    clear_db()
    add_round_stats(1,5,4,2)
    add_debaters(num_teams*2)
    add_schools(num_teams/5)
    add_teams()
    add_rooms(num_teams/2)
    add_judges(num_teams/2)
    add_check_in(1)



def enter_results(round_num):
    for r in Round.objects.filter(round_number = round_num):
        d = [[r.gov_team.debaters.all()[0],0],[r.gov_team.debaters.all()[1],0],[r.opp_team.debaters.all()[0],0],[r.opp_team.debaters.all()[1],0]]
        i = 0
        for deb in d:
            s = random.randint(230,270)/10.0
            d[i][1] = s
            i+=1
        a = sorted(d, key = lambda speak: speak[1])
        for i in range(len(d)):
            RoundStats.objects.create(debater = a[i][0], round = r, speaks = a[i][1], ranks = 4-i)
        if d[0][1]+d[1][1] > d[2][1]+d[3][1]:
            r.victor=1
        elif d[0][1]+d[1][1] < d[2][1]+d[3][1]:
            r.victor=2
        else:
            if a.index(d[0])+a.index(d[1]) < a.index(d[2]) + a.index(d[3]):
                r.victor=1
            else:
                r.victor=2
        r.save()
        
def add_debaters(num_gen):
    for i in range(num_gen):
        Debater.objects.create(name = str(random.random()), novice_status = random.randint(0,1))

def add_schools(num_gen):
    for i in range(num_gen):
        School.objects.create(name = str(random.random()))
                              
def add_teams():
    myDebaters = Debater.objects.all()
    schools = School.objects.all()
    for i in range(len(myDebaters)/2):
        t = Team(name = str(random.random()), school = schools[random.randint(0,len(schools)-1)], seed = random.randint(0,3)) 
        t.save()
        t.debaters = myDebaters[i], myDebaters[len(myDebaters)-1-i]
        t.save()

def add_rooms(num_gen):
    for i in range(num_gen):
        Room.objects.create(name = str(random.random()), rank = random.randint(0,10))

def add_judges(num_gen):
    schools = School.objects.all()
    for i in range(num_gen):
        Judge.objects.create(name = str(random.random()), rank = random.randint(0,10), school = schools[random.randint(0,len(schools)-1)])

def add_check_in(round_num):
    judges = Judge.objects.all()
    for i in range(len(judges)):
        CheckIn.objects.create(judge = judges[i], round_number = round_num)

def add_round_stats(cur_round, num_rounds, var_break, nov_break):
    TabSettings.objects.create(key = "cur_round", value = cur_round)
    TabSettings.objects.create(key = "tot_rounds", value = num_rounds)
    TabSettings.objects.create(key = "var_teams_to_break", value = var_break)
    TabSettings.objects.create(key = "nov_teams_to_break", value = nov_break)
    
def clear_db():
    check_ins = CheckIn.objects.all()
    for i in range(len(check_ins)):
        CheckIn.delete(check_ins[i])

    round_stats = RoundStats.objects.all()
    for i in range(len(round_stats)):
        RoundStats.delete(round_stats[i])
        
    rounds = Round.objects.all()
    for i in range(len(rounds)):
        Round.delete(rounds[i])
        
    judges = Judge.objects.all()
    for i in range(len(judges)):
        Judge.delete(judges[i])
        
    rooms = Room.objects.all()
    for i in range(len(rooms)):
        Room.delete(rooms[i])
        
    scratches = Scratch.objects.all()
    for i in range(len(scratches)):
        Scratch.delete(scratches[i])
        
    tab_set = TabSettings.objects.all()
    for i in range(len(tab_set)):
        TabSettings.delete(tab_set[i])
        
    teams = Team.objects.all()
    for i in range(len(teams)):
        Team.delete(teams[i])

    debaters = Debater.objects.all()
    for i in range(len(debaters)):
        Debater.delete(debaters[i])

    schools = School.objects.all()
    for i in range(len(schools)):
        School.delete(schools[i])
    
#Here are a bunch of test cases. Should run master test to run all and ensure all functionality

#Here are the actual tests
def master():
    test_not_enough_judges()
    print "not enough judges"
    test_not_enough_judges_checked_in()
    print "not enough judges checked in"
    test_not_enough_rooms()
    print "not enough rooms"
    test_impossible_judges()
    print "impossible judges"
    test_scale()
    print "scale"
    test_judge_corner()
    print "judge corner"
    test_full_tourney()
    print "full tourney"
    test_odd()
    print "odd"
    test_iron_man()
    print "iron man"
    
#Make sure an error gets thrown if there aren't enough judges in the round
def test_not_enough_judges():
    create_tourney(10)
    judges = Judge.objects.all()
    Judge.delete(judges[0])
    threwException = False
    try:
        tab_logic.pair_round()
    except errors.NotEnoughJudgesError:
        threwException = True
    finally:
        assert threwException == True

#Make sure an error gets thrown if there aren't enough judges checked in
def test_not_enough_judges_checked_in():
    create_tourney(10)
    checked_in_judges = CheckIn.objects.all()
    CheckIn.delete(checked_in_judges[0])
    threwException = False
    try: 
        tab_logic.pair_round()
    except errors.NotEnoughJudgesError:
        threwException = True
    finally:
        assert threwException == True

#Make sure an error gets thrown if there aren't enough rooms
def test_not_enough_rooms():
    create_tourney(10)
    rooms = Room.objects.all()
    Room.delete(rooms[0])
    threwException = False
    try:
        tab_logic.pair_round()
    except errors.NotEnoughRoomsError:
        threwException = True
    finally:
        assert threwException == True

#Make sure it throws an error if there are too many scratches to form valid pairings
def test_impossible_judges():
    clear_db()
    add_round_stats(1,5,0,0)
    
    Debater.objects.create(name = "DebA", novice_status = 0)
    Debater.objects.create(name = "DebB", novice_status = 0)
    Debater.objects.create(name = "DebC", novice_status = 0)
    Debater.objects.create(name = "DebD", novice_status = 0)
    Debater.objects.create(name = "DebE", novice_status = 0)
    Debater.objects.create(name = "DebF", novice_status = 0)
    Debater.objects.create(name = "DebG", novice_status = 0)
    Debater.objects.create(name = "DebH", novice_status = 0)
    
    School.objects.create(name = "SchoolA")
    School.objects.create(name = "SchoolB")
    School.objects.create(name = "SchoolC")
    School.objects.create(name = "SchoolD")
    School.objects.create(name = "SchoolE")

    t = Team(name = "TeamA", school = School.objects.get(name = "SchoolA"), seed = 3)
    t.save()
    t.debaters = Debater.objects.get(name = "DebA"), Debater.objects.get(name = "DebB")
    t.save()

    t = Team(name = "TeamB", school = School.objects.get(name = "SchoolB"), seed = 2)
    t.save()
    t.debaters = Debater.objects.get(name = "DebC"), Debater.objects.get(name = "DebD")
    t.save()
    
    t = Team(name = "TeamC", school = School.objects.get(name = "SchoolC"), seed = 1)
    t.save()
    t.debaters = Debater.objects.get(name = "DebE"), Debater.objects.get(name = "DebF")
    t.save()

    t = Team(name = "TeamD", school = School.objects.get(name = "SchoolD"), seed = 0)
    t.save()
    t.debaters = Debater.objects.get(name = "DebG"), Debater.objects.get(name = "DebH")
    t.save()
    
    Room.objects.create(name = "RoomA", rank = 1)
    Room.objects.create(name = "RoomB", rank = 2)

    Judge.objects.create(name = "JudgeA", rank = 10, school = School.objects.get(name ="SchoolA"))
    Judge.objects.create(name = "JudgeD", rank = 0, school = School.objects.get(name = "SchoolD"))

    add_check_in(1)

    Scratch.objects.create(judge = Judge.objects.get(name = "JudgeA"), team = Team.objects.get(name = "TeamA"), scratch_type = 1)
    Scratch.objects.create(judge = Judge.objects.get(name = "JudgeD"), team = Team.objects.get(name = "TeamD"), scratch_type = 1)
    
    threwException = False
    try:
        tab_logic.pair_round()
    except errors.ToManyScratchesError:
        threwException = True
    finally:
        assert threwException == True

#Make sure it doesn't take too long.  Don't actually verify correct results here
def test_scale():
    create_tourney(100)
    beg_time = time.time()
    tab_logic.pair_round()
    end_time = time.time()
    assert end_time-beg_time <=120
    enter_results(1)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 2
    r.save()
    add_check_in(2)
    beg_time = time.time()
    tab_logic.pair_round()
    end_time = time.time()
    assert end_time-beg_time <=120
    enter_results(2)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 3
    r.save()
    add_check_in(3)
    beg_time = time.time()
    tab_logic.pair_round()
    end_time = time.time()
    assert end_time-beg_time <=120
    enter_results(3)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 4
    r.save()
    add_check_in(4)
    beg_time = time.time()
    tab_logic.pair_round()
    end_time = time.time()
    print beg_time-end_time
    assert end_time-beg_time <=120
    enter_results(4)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 5
    r.save()
    add_check_in(5)
    beg_time = time.time()
    tab_logic.pair_round()
    end_time = time.time()
    assert end_time-beg_time <=120
    enter_results(5)

    r = TabSettings.objects.get(key = "cur_round")
    r.value = 6
    r.save()
    
    beg_time = time.time()
    var_break()
    nov_break()
    var_speaks()
    nov_speaks()
    end_time = time.time()
    assert end_time-beg_time <=120

#test that it works even if the last judge can't be assigned to the last team
def test_judge_corner():
    clear_db()
    add_round_stats(1,5,0,0)
    
    Debater.objects.create(name = "DebA", novice_status = 0)
    Debater.objects.create(name = "DebB", novice_status = 0)
    Debater.objects.create(name = "DebC", novice_status = 0)
    Debater.objects.create(name = "DebD", novice_status = 0)
    Debater.objects.create(name = "DebE", novice_status = 0)
    Debater.objects.create(name = "DebF", novice_status = 0)
    Debater.objects.create(name = "DebG", novice_status = 0)
    Debater.objects.create(name = "DebH", novice_status = 0)
    
    School.objects.create(name = "SchoolA")
    School.objects.create(name = "SchoolB")
    School.objects.create(name = "SchoolC")
    School.objects.create(name = "SchoolD")
    School.objects.create(name = "SchoolE")

    t = Team(name = "TeamA", school = School.objects.get(name = "SchoolA"), seed = 3)
    t.save()
    t.debaters = Debater.objects.get(name = "DebA"), Debater.objects.get(name = "DebB")
    t.save()

    t = Team(name = "TeamB", school = School.objects.get(name = "SchoolB"), seed = 2)
    t.save()
    t.debaters = Debater.objects.get(name = "DebC"), Debater.objects.get(name = "DebD")
    t.save()
    
    t = Team(name = "TeamC", school = School.objects.get(name = "SchoolC"), seed = 1)
    t.save()
    t.debaters = Debater.objects.get(name = "DebE"), Debater.objects.get(name = "DebF")
    t.save()

    t = Team(name = "TeamD", school = School.objects.get(name = "SchoolD"), seed = 0)
    t.save()
    t.debaters = Debater.objects.get(name = "DebG"), Debater.objects.get(name = "DebH")
    t.save()
    
    Room.objects.create(name = "RoomA", rank = 1)
    Room.objects.create(name = "RoomB", rank = 2)

    Judge.objects.create(name = "JudgeA", rank = 10, school = School.objects.get(name ="SchoolA"))
    Judge.objects.create(name = "JudgeB", rank = 0, school = School.objects.get(name = "SchoolE"))

    add_check_in(1)

    Scratch.objects.create(judge = Judge.objects.get(name = "JudgeA"), team = Team.objects.get(name = "TeamA"), scratch_type = 1)

    tab_logic.pair_round()
    rounds = Round.objects.all()
    assert rounds[0].judge == Judge.objects.get(name = "JudgeB")
    assert rounds[1].judge == Judge.objects.get(name = "JudgeA")
    
#This will verify that a full tournament of 32-teams works
def test_full_tourney():
    #start a new tournament

    #Seed random number generator so that can verify correct results
    random.seed(0)
    
    clear_db()
    add_round_stats(1,5,4,2)

    #create 64 debaters
    for i in range(32):
        nov_stat1 = 0
        nov_stat2 = 0
        if i%3 == 0: #make some people novices
            nov_stat1 = 1
            if i > 10:
                nov_stat2 = 1
                
        Debater.objects.create(name = "Debater1fromT" + str(i), novice_status = nov_stat1)
        Debater.objects.create(name = "Debater2fromT" + str(i), novice_status = nov_stat2)

    #create 8 schools
    for i in range(8):
        School.objects.create(name = "School" + str(i))
    #create host school for scratch/judge purposes
    School.objects.create(name = "HostSchool")
    
    #create 32 teams                        
    for i in range(32):
        school_num = i%8
        mySchool = School.objects.get(name = "School" + str(school_num))                      
        t = Team(name = "Team" + str(i), school = mySchool, seed = i%4)
        t.save()
        deb1 = Debater.objects.get(name = "Debater" + str(1) + "fromT" + str(i))
        deb2 = Debater.objects.get(name = "Debater" + str(2) + "fromT" + str(i))
        t.debaters = deb1, deb2
        t.save()

    #add some rooms
    add_rooms(32)

    #add judges
    #add 16 judges from host school
    
    for i in range(8):
        Judge.objects.create(name = "JudgeFromHost" + str(i), rank = i, school = School.objects.get(name = "HostSchool"))
    #add 2 judges from each competeing school
    for i in range(8):
        Judge.objects.create(name = "JudgeFromSchool" + str(i), rank = i*2+.3, school = School.objects.get(name = "School" + str(i)))

    #check in all judges
    add_check_in(1)

    #pair first round 
    tab_logic.pair_round()

    #Check that all full seed teams are hitting unseeded and all free seeds are hitting half-seeds
    for p in Round.objects.filter(round_number = 1):
        if p.gov_team.seed == 0:
            assert p.opp_team.seed == 3
        elif p.gov_team.seed == 1:
            assert p.opp_team.seed == 2
        elif p.gov_team.seed == 2:
            assert p.opp_team.seed == 1
        else:
            assert p.opp_team.seed == 0

        #That judge and both teams are from different schools
        assert p.judge.school != p.gov_team.school
        assert p.judge.school != p.opp_team.school
        assert p.gov_team.school != p.opp_team.school

        #Check that scratches are obeyed
        assert len(Scratch.objects.filter(judge = p.judge, team = p.gov_team)) == 0
        assert len(Scratch.objects.filter(judge = p.judge, team = p.opp_team)) == 0

    add_check_in(2)
    
    try:
        tab_logic.ready_to_pair(2)
    except errors.PrevRoundNotEnteredError:
        assert True
    else:
        assert False
        
        
    enter_results(1)

    #Check that results are as expected

    #check_results()

    r = TabSettings.objects.get(key = "cur_round")
    r.value = 2
    r.save()
    tab_logic.pair_round()
    enter_results(2)

    r = TabSettings.objects.get(key = "cur_round")
    r.value = 3
    r.save()
    add_check_in(3)
    tab_logic.pair_round()
    enter_results(3)

    r = TabSettings.objects.get(key = "cur_round")
    r.value = 4
    r.save()
    add_check_in(4)
    tab_logic.pair_round()
    enter_results(4)

    r = TabSettings.objects.get(key = "cur_round")
    r.value = 5
    r.save()
    add_check_in(5)
    tab_logic.pair_round()
    enter_results(5)

    check_results("ResultsOfTournament.xls")

    var_break()
    nov_break()
    var_speaks()
    nov_speaks()

    





    
def check_results(f):
    fileName = f
    data = xlrd.open_workbook(fileName)
    for i in range(TabSettings.objects.get(key="cur_round").value-1):
        s = data.sheets()[i]
        line = 1
        stillData = True
        while s.cell_type == 1:
            assert len(Round.objects.filter(round_number = s.cell(1,line), gov_team = s.cell(2,line), opp_team = s.cell(3,line),
                                        judge = s.cell(4,line), pull_up = s.cell(5,line), room = s.cell(6,line), victor = s.cell(7,line))) == 1
            assert len(RoundStats.objects.filter(round = s.cell(0,line), debater = s.cell(8,line), speaks = s.cell(9,line), ranks = s.cell(10,line))) == 1
            assert len(RoundStats.objects.filter(round = s.cell(0,line), debater = s.cell(11,line), speaks = s.cell(12,line), ranks = s.cell(13,line))) == 1
            assert len(RoundStats.objects.filter(round = s.cell(0,line), debater = s.cell(14,line), speaks = s.cell(15,line), ranks = s.cell(16,line))) == 1
            assert len(RoundStats.objects.filter(round = s.cell(0,line), debater = s.cell(17,line), speaks = s.cell(18,line), ranks = s.cell(19,line))) == 1
            line+=1  
    
    
#Make sure stuff works with an odd number of teams
def test_odd():
    random.seed(1)
    create_tourney(21)
    tab_logic.pair_round()
    enter_results(1)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 2
    r.save()
    add_check_in(2)
    tab_logic.pair_round()
    enter_results(2)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 3
    r.save()
    add_check_in(3)
    tab_logic.pair_round()
    enter_results(3)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 4
    r.save()
    add_check_in(4)
    tab_logic.pair_round()
    enter_results(4)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 5
    r.save()
    add_check_in(5)
    tab_logic.pair_round()
    enter_results(5)
    check_results("ResultsOdd.xls")

    r = TabSettings.objects.get(key = "cur_round")
    r.value = 6
    r.save()
    var_break()
    nov_break()
    var_speaks()
    nov_speaks()

#Make sure stuff works for iron men
def test_iron_man():
    #Speaks are correct during round.
    #Ranking at end is correct (this is tested later so don't have to test here)
    random.seed(1)
    create_tourney(25)
    tab_logic.pair_round()
    enter_results_iron(1)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 2
    r.save()
    add_check_in(2)
    tab_logic.pair_round()
    enter_results(2)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 3
    r.save()
    add_check_in(3)
    tab_logic.pair_round()
    enter_results(3)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 4
    r.save()
    add_check_in(4)
    tab_logic.pair_round()
    enter_results(4)
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 5
    r.save()
    add_check_in(5)
    tab_logic.pair_round()
    enter_results(5)

    check_results("ResultsIronMan.xls")
    r = TabSettings.objects.get(key = "cur_round")
    r.value = 6
    r.save()
    var_break()
    nov_break()
    var_speaks()
    nov_speaks()
    
    
    
def enter_results_iron(round_num):
    entered_iron_man = 0
    for r in Round.objects.filter(round_number = round_num):
        if entered_iron_man == 0:
            d = [[r.gov_team.debaters.all()[0],0],[r.gov_team.debaters.all()[0],0],[r.opp_team.debaters.all()[0],0],[r.opp_team.debaters.all()[1],0]]
            entered_iron_man +=1
        elif entered_iron_man == 1:
            d = [[r.gov_team.debaters.all()[0],0],[r.gov_team.debaters.all()[1],0],[r.opp_team.debaters.all()[1],0],[r.opp_team.debaters.all()[1],0]]
            entered_iron_man +=1
        else:
            d = [[r.gov_team.debaters.all()[0],0],[r.gov_team.debaters.all()[1],0],[r.opp_team.debaters.all()[0],0],[r.opp_team.debaters.all()[1],0]]
        i = 0
        for deb in d:
            s = random.randint(230,270)/10.0
            d[i][1] = s
            i+=1
        a = sorted(d, key = lambda speak: speak[1])
        for i in range(len(d)):
            RoundStats.objects.create(debater = a[i][0], round = r, speaks = a[i][1], ranks = 4-i)
        if d[0][1]+d[1][1] > d[2][1]+d[3][1]:
            r.victor=1
        elif d[0][1]+d[1][1] < d[2][1]+d[3][1]:
            r.victor=2
        else:
            if a.index(d[0])+a.index(d[1]) < a.index(d[2]) + a.index(d[3]):
                r.victor=1
            else:
                r.victor=2
        r.save()

#Make sure varsity break works
def var_break():
    #Should include novice teams if good enough
    #Make sure didn't try to break too many teams
    #If team missed a round, but should be allowed to break, make sure that works here.
    #Basically, give them a loss, but don't assign any roundstats because should get average of speaks/ranks for other rounds.

    random.seed(1)
    var_break = tab_logic.tab_var_break()
    return var_break
    

#Make sure novice break works
def nov_break():
    #Only novice teams
    #If broke varsity not included
    #Make sure didn't try to break too many novice teams

    random.seed(1)
    nov_break = tab_logic.tab_nov_break()
    return nov_break

#Make sure varsity speaks are calculated correctly
def var_speaks():
    #should include everyone
    #should work even if iron man round

    random.seed(1)
    var_speakers = tab_logic.rank_speakers()
    return var_speakers

#Make sure novice speaks are calculated correctly
def nov_speaks():
    #Includes novices all novices even if got varsity speaker aware
    #works with iron man round

    random.seed(1)
    nov_speakers = tab_logic.rank_nov_speakers()
    return nov_speakers

        

    #We need a way to deal with giving both teams a bye or both teams a forfeit.  Maybe an extra option? Could happen if judge doesn't show up or if somehow both teams don't show

        
        
            

