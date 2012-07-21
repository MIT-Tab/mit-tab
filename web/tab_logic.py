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

#Julia's file


#Always sort so that top team is at the beginning of the list - use reverse!

from tab.models import *
from django.db.models import *

import random
import pairing_alg
import assignJudges
import errors
from decimal import *
import send_texts
from datetime import datetime

    
#will return false if not ready to pair yet
def pair_round():
    #pairings = [gov,opp,judge,room]
    try:
        ready_to_pair(TabSettings.objects.get(key="cur_round").value)
    except errors.NotEnoughJudgesError:
        raise errors.NotEnoughJudgesError()
    except errors.NotEnoughRoomsError:
        raise errors.NotEnoughRoomsError()
    random.seed(0xBEEF)
    
    #add scratches for teams/judges from the same school if they haven't already been added
    add_scratches_for_school_affil()


    list_of_teams = [None]*TabSettings.objects.get(key="cur_round").value
    pull_up = None

    #Record no-shows
    forfeit_teams = list(Team.objects.filter(checked_in=False))
    for t in forfeit_teams:
        n = NoShow(no_show_team = t, round_number = TabSettings.objects.get(key="cur_round").value)
        n.save()
    

    
    
    #if it is the first round, pair by seed
    if TabSettings.objects.get(key="cur_round").value == 1:
        list_of_teams = list(Team.objects.filter(checked_in=True))
        b = Bye(bye_team = list_of_teams[random.randint(0,len(list_of_teams)-1)], round_number = TabSettings.objects.get(key="cur_round").value)
        b.save()
        list_of_teams.remove(b.bye_team)
        list_of_teams = sorted(list_of_teams, key=lambda team: team.seed, reverse = True)

        #print list_of_teams
            
        
        #should have sorted order of teams.  Now just need to do pairings.

        #pairings are [gov,opp,judge,room]
        #pairings = [None]* Team.objects.count()/2

    #Otherwise, pair by speaks
    else:
        #print list_of_teams
        bye_teams = [bye.bye_team for bye in Bye.objects.all()]
        
        for r in Round.objects.filter(victor = 3):
            print r.round_number
            print str(r.gov_team) + " won via forfeit"
            bye_teams += [r.gov_team]
        for r in Round.objects.filter(victor = 4):
            bye_teams += [r.opp_team]
            print r.round_number
            print str(r.opp_team) + " won via forfeit"
        random.shuffle(bye_teams, random= random.random)
        print "Final bye team list: ", bye_teams 
        for i in range(TabSettings.objects.get(key="cur_round").value): #sort the teams within each bracket
            list_of_teams[i] = []  # set the list for the current bracket to nothing
            #print "i is: " + str(i)
            for t in list(Team.objects.filter(checked_in=True)):  #for each of the teams
                bye = had_bye(t)  #check if this team has had the bye, is true if did
                num_wins = tot_wins(t)  #check how many wins the team has had.  Round 1 should be zero.
                print t.name, " => ", bye_teams.count(t)
                if num_wins == i:  #assign to correct bracket based on number of wins.  Teams with zero wins in 0th array slot, teams with 1 win in 1st spot, etc.
                    if TabSettings.objects.get(key="cur_round").value-bye_teams.count(t) != 1: 
                        list_of_teams[i] += [t]

            list_of_teams[i] = rank_teams_except_record(list_of_teams[i]) #sort the teams within each bracket such that highest team is first

        if len(bye_teams) != 0:
            for t in list(Team.objects.filter(checked_in=True)):
                if TabSettings.objects.get(key="cur_round").value-bye_teams.count(t) == 1: #if teams have won by forfeit/bye each round, pair into the middle
                    print t
                    list_of_teams[TabSettings.objects.get(key="cur_round").value-1].insert(int(float(len(list_of_teams[tot_wins(t)]))/2.0),t)
        print "these are the teams before pullups"
        print "list_of_teams"
        #even out brackets with pull-up, etc. if necessary
        print list_of_teams
        for bracket in reversed(range(TabSettings.objects.get(key="cur_round").value)):
            if len(list_of_teams[bracket])/2 != len(list_of_teams[bracket])/2.0:
                #print "need pull-up"
                #If there are no teams all down, give the bye to a one down team.
                if bracket == 0: #in bottom bracket so give bye instead of pulling up
                    byeint = len(list_of_teams[0])-1
                    b = Bye(bye_team = list_of_teams[0][byeint], round_number = TabSettings.objects.get(key="cur_round").value)
                    b.save()
                    list_of_teams[0].remove(list_of_teams[0][byeint])
                elif bracket == 1 and len(list_of_teams[0]) == 0: #in 1 up and no all down teams
                    found_bye = False
                    for byeint in range(len(list_of_teams[1])-1, -1, -1):
                        if had_bye(list_of_teams[1][byeint]):
                            pass
                        elif found_bye == False:
                            b = Bye(bye_team = list_of_teams[1][byeint], round_number = TabSettings.objects.get(key="cur_round").value)
                            b.save()
                            list_of_teams[1].remove(list_of_teams[1][byeint])
                            found_bye = True
                    if found_bye == False:
                        raise errors.NotEnoughTeamsError()
                    
                else: #need to pull a team up
                    pull_up = None
                    i = len(list_of_teams[bracket-1])-1 # i is the last team in the bracket below
                    teams_been_pulled_up = Round.objects.exclude(pullup=None)   
                    while pull_up == None: #find the lowest team in bracket below that can be pulled up
                        if list_of_teams[bracket-1][i] not in teams_been_pulled_up:
                            pull_up = list_of_teams[bracket-1][i]
                            list_of_teams[bracket].append(pull_up)
                            list_of_teams[bracket-1].remove(pull_up)
                            #after adding pull-up to new bracket and deleting from old, sort again by speaks making sure to leave any first
                            #round bye in the correct spot
                            removed_teams = []
                            for t in list(Team.objects.filter(checked_in=True)):
                                #They have all wins and they haven't forfeited so they need to get paired in
                                if TabSettings.objects.get(key="cur_round").value-bye_teams.count(t) == 1 and tot_wins(t) == bracket:
                                    removed_teams += [t]
                                    list_of_teams[bracket].remove(t)
                            list_of_teams[bracket] = rank_teams_except_record(list_of_teams[bracket])
                            print "list of teams in " + str(bracket) + " except removed"
                            print list_of_teams[bracket]
                            for t in removed_teams:
                                list_of_teams[bracket].insert(len(list_of_teams[bracket])/2,t)
                        else:
                            i-=1
    print "these are the teams after pullups"
    print list_of_teams
        
        #should have sorted order of teams.  Now just need to do pairings.  Pass list to pairings algorithm

    #if len(list_of_teams)%2 == 1:
    #    raise errors.ByeAssignmentError()

    #Count every team
    #I don't understand why you guys use this idiom of [None]*N, then slotting
    #pairings into each of the slots. Why not just use a blank array and append?
    #Or, since you're going to shuffle them eventually, just use a set.
    pairings = [None]*(Team.objects.filter(checked_in=True).count()/2)
    #print len(pairings)
    i=0
    for bracket in range(TabSettings.objects.get(key="cur_round").value):
        if TabSettings.objects.get(key="cur_round").value == 1:
            temp = pairing_alg.perfectPairing(list_of_teams)
        else:
            temp = pairing_alg.perfectPairing(list_of_teams[bracket])
        for pair in temp:
            pairings[i] = [pair[0],pair[1],[None],[None]]
            i+=1

    #print "pairings"
    #print pairings
        


    #should randomize first
    if TabSettings.objects.get(key="cur_round").value == 1:
        random.shuffle(pairings, random= random.random)
        pairings = sorted(pairings, key = lambda team: highest_seed(team[0],team[1]), reverse = True)
    else: #sort with pairing with highest ranked team first
        sorted_teams = rank_teams()
        print sorted_teams
        print "pairings"
        print pairings
        pairings = sorted(pairings, key=lambda team: min(sorted_teams.index(team[0]), sorted_teams.index(team[1])))
        
                          

        
    #assign judges
    pairings = assignJudges.addJudges(pairings)
    
    #assign rooms (does this need to be random? maybe bad to have top ranked teams/judges in top rooms?)
    rooms = Room.objects.all()
    rooms = sorted(rooms, key=lambda r: r.rank, reverse = True)
    #print rooms

    for i in range(len(pairings)):
        pairings[i][3] = rooms[i]
    
    #print "pairings with rooms, etc"
    #print pairings

    #enter into database
    for p in pairings:
        r = Round(round_number = TabSettings.objects.get(key="cur_round").value, gov_team = p[0], opp_team = p[1], judge = p[2], room = p[3])
        if p[0] == pull_up:
            r.pullup = 1
        elif p[1] == pull_up:
            r.pullup = 2
        r.save()


#check if there are enough judges and rooms to pair and if all of the previous rounds have been entered  This still doesn't entirely work.
#check if all results have been entered
def ready_to_pair(round_to_check):
    if CheckIn.objects.filter(round_number = round_to_check).count() < Team.objects.filter(checked_in=True).count()/2:
        raise errors.NotEnoughJudgesError()
    elif Room.objects.all().count() < Team.objects.filter(checked_in=True).count()/2:
        raise errors.NotEnoughRoomsError()
    elif round_to_check != 1 and RoundStats.objects.all().count() < Round.objects.filter(round_number = round_to_check-1).count():
        raise errors.PrevRoundNotEnteredError()
    else:
        prev_rounds = Round.objects.filter(round_number = round_to_check-1)
        for r in prev_rounds:
            if r.victor == 0:
                raise errors.PrevRoundNotEnteredError()
    
    return True

#add scratches for teams/judges from the same school if they haven't already been added
def add_scratches_for_school_affil():
    for j in Judge.objects.all():
        for t in Team.objects.all():
            if j.school == t.school:
                if Scratch.objects.filter(judge = j, team = t).count() == 0:
                    Scratch.objects.create(judge = j,team = t,scratch_type = 1)
    
def highest_seed(t1,t2):
    if t1.seed > t2.seed:
        return t1.seed
    else:
        return t2.seed
    
def highest_speak(t1,t2):
    if tot_speaks(t1) > tot_speaks(t2):
        return tot_speaks(t1)
    else:
        return tot_speaks(t2)

def most_wins(t1,t2):
    if tot_wins(t1) > tot_wins(t2):
        return tot_wins(t1)
    else:
        return tot_wins(t2)

#Check if two teams have hit before                          
def hit_before(t1, t2):
    if Round.objects.filter(gov_team = t1, opp_team = t2).count() > 0:
        return True
    elif Round.objects.filter(gov_team = t2, opp_team = t1).count() > 0:
        return True
    else:
        return False
    
#This should calculate whether or not team t has hit the pull-up before.
def hit_pull_up(t):
    for a in list(Round.objects.filter(gov_team = t)):
        if a.pullup == 2:
            return True
    for a in list(Round.objects.filter(opp_team = t)):
        if a.pullup == 1:
            return True
    return False

def num_opps(t):
    return Round.objects.filter(opp_team = t).count()

def num_govs(t):
    return Round.objects.filter(gov_team = t).count()


#Return True if the team has already had the bye
def had_bye(t):
    if Bye.objects.filter(bye_team = t).count() > 0:
        return True
    else:
        return False

def num_byes(t):
    return Bye.objects.filter(bye_team = t).count()

def num_forfeit_wins(t):
    forfeit_wins = 0
    for r in list(Round.objects.filter(gov_team = t)):
        if r.victor == 3:
            forfeit_wins +=1
    for r in list(Round.objects.filter(opp_team = t)):
        if r.victor == 4:
            forfeit_wins +=1
    return forfeit_wins

def num_no_show(t):
    return NoShow.objects.filter(no_show_team = t).count()

#Return true if the team forfeited the round, otherwise, return false
def forfeited_round(r,t):
    if Round.objects.filter(gov_team = t, round_number = r.round_number).count() > 0:
        if r.victor == 4:
            return True
    elif Round.objects.filter(opp_team = t, round_number = r.round_number).count() > 0:
        if r.victor == 3:
            return True
    else:
        return False


###Return true if the team won the round because the other team forfeited, otherwise return false
def won_by_forfeit(r,t):
    if Round.objects.filter(gov_team = t, round_number = r.round_number).count() > 0:
        if r.victor == 3:
            return True
    elif Round.objects.filter(opp_team = t, round_number = r.round_number).count() > 0:
        if r.victor == 4:
            return True
    else:
        return False


               
    
#Calculate the total number of wins a team has
def tot_wins(team):
    tot_wins = Round.objects.filter(Q(gov_team=team, victor=Round.GOV)|
                                    Q(opp_team=team, victor=Round.OPP)).count()
    #If a team had the bye, they won't have a round for that win so add one win
    tot_wins += num_byes(team) 
    return tot_wins

#Calculate the team's average speaks
def avg_team_speaks(t):
    tot_speaks = 0
    for d in list(t.debaters.all()):
        debStats = list(RoundStats.objects.filter(debater = d))
        for r in debStats:
            tot_speaks += r.speaks
            
    if TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1 == 0:
        return 0
    else:
        return float(tot_speaks)/float(TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1)


#Calculate the team's average ranks
def avg_team_ranks(t):
    tot_ranks = 0
    for d in t.debaters.all():
        debStats = list(RoundStats.objects.filter(debater = d))
        for r in debStats:
            if won_by_forfeit(r.round, t):
                pass
            elif forfeited_round(r.round,t):
                tot_ranks += 3.5
            else:
                tot_ranks += r.ranks
    for n in NoShow.objects.filter(no_show_team = t):
        tot_ranks +=7
            
    if TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1 == 0:
        return 0
    else:
        return float(tot_ranks)/float(TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1)

#Calculate a team's total speaks
def tot_speaks(team):
    tot_speaks = sum([tot_speaks_deb(deb)
                      for deb in team.debaters.all()])
    tot_speaks = float(tot_speaks) + avg_team_speaks(team) * (num_byes(team) + num_forfeit_wins(team))
    return tot_speaks


def tot_ranks(team):
    tot_ranks = 0
    for debater in team.debaters.all():
        for round_stat in debater.roundstats_set.all():
            if won_by_forfeit(round_stat.round, team):
                #The average ranks will be added later
                pass
            elif forfeited_round(round_stat.round, team):
                tot_ranks += 3.5
            else:
                tot_ranks += round_stat.ranks
    for n in list(NoShow.objects.filter(no_show_team = team)):
        tot_ranks +=7

    #If a team had the bye or won by a forfeit, add in the average of ranks for those rounds
    tot_ranks += avg_team_ranks(team) * (num_byes(team) + num_forfeit_wins(team))
        
    return tot_ranks
                          
                          
def single_adjusted_speaks(t):
    if TabSettings.objects.get(key = "cur_round").value < 3:
        return tot_speaks(t)
    elif TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t)+num_no_show(t)) < 3 :
        return avg_team_speaks(t)*(TabSettings.objects.get(key = "cur_round").value-2)
    
    if TabSettings.objects.get(key = "cur_round").value > TabSettings.objects.get(key = "tot_rounds").value:
        num_rounds = TabSettings.objects.get(key = "tot_rounds").value
    else:
        num_rounds = TabSettings.objects.get(key = "cur_round").value
    listOfSpeaks = []
    for i in range(num_rounds):
        listOfSpeaks += [None]
        
    for d in list(t.debaters.all()):
        debStats = RoundStats.objects.filter(debater = d)
        for r in debStats:
            if listOfSpeaks[r.round.round_number-1] == None:
                listOfSpeaks[r.round.round_number-1] = r.speaks
            else:
                listOfSpeaks[r.round.round_number-1] += r.speaks
            
    while None in listOfSpeaks:
        listOfSpeaks.remove(None)
        
    listOfSpeaks.sort()

    listOfSpeaks=listOfSpeaks[1:-1]
    sing_adj_speaks = 0
    for s in listOfSpeaks:
        sing_adj_speaks+=float(s)
    if had_bye(t) == True:
        sing_adj_speaks+= avg_team_speaks(t)*(num_byes(t)+num_forfeit_wins(t))
    return sing_adj_speaks

def single_adjusted_ranks(t):
    if TabSettings.objects.get(key = "cur_round").value < 3:
        return tot_ranks(t)
    elif TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t)+num_no_show(t)) < 3 :
        return avg_team_ranks(t)*(TabSettings.objects.get(key = "cur_round").value-2)
    
    if TabSettings.objects.get(key = "cur_round").value > TabSettings.objects.get(key = "tot_rounds").value:
        num_rounds = TabSettings.objects.get(key = "tot_rounds").value
    else:
        num_rounds = TabSettings.objects.get(key = "cur_round").value
    list_of_ranks = []
    for i in range(num_rounds):
        list_of_ranks += [None]

    for d in list(t.debaters.all()):
        debStats = RoundStats.objects.filter(debater = d)
        for r in debStats:
            if list_of_ranks[r.round.round_number-1] == None:
                if won_by_forfeit(r.round, t):
                    pass
                elif forfeited_round(r.round,t):
                    list_of_ranks[r.round.round_number-1] = 3.5
                else:
                    list_of_ranks[r.round.round_number-1] = r.ranks
            else:
                if won_by_forfeit(r.round, t):
                    pass
                elif forfeited_round(r.round,t):
                    list_of_ranks[r.round.round_number-1] += 3.5
                else:
                    list_of_ranks[r.round.round_number-1] += r.ranks
    for n in list(NoShow.objects.filter(no_show_team=t)):
        list_of_ranks[n.round_number-1] = 7

    while None in list_of_ranks:
        list_of_ranks.remove(None)
        
    list_of_ranks.sort()

    list_of_ranks=list_of_ranks[1:-1]
    sing_adj_ranks = 0
    for r in list_of_ranks:
        sing_adj_ranks+=float(r)
    if had_bye(t) == True:
        sing_adj_ranks+= avg_team_ranks(t)*(num_byes(t)+num_forfeit_wins(t))
    return sing_adj_ranks


def double_adjusted_speaks(t):
    if TabSettings.objects.get(key = "cur_round").value < 5:
        return tot_speaks(t)
    elif TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t)+num_no_show(t)) < 5 :
        return avg_team_speaks(t)*(TabSettings.objects.get(key = "cur_round").value-4)

    if TabSettings.objects.get(key = "cur_round").value > TabSettings.objects.get(key = "tot_rounds").value:
        num_rounds = TabSettings.objects.get(key = "tot_rounds").value
    else:
        num_rounds = TabSettings.objects.get(key = "cur_round").value
    listOfSpeaks = []
    for i in range(num_rounds):
        listOfSpeaks += [None]

    for d in list(t.debaters.all()):
        debStats = RoundStats.objects.filter(debater = d)
        for r in debStats:
            if listOfSpeaks[r.round.round_number-1] == None:
                listOfSpeaks[r.round.round_number-1] = r.speaks
            else:
                listOfSpeaks[r.round.round_number-1] += r.speaks
            
    listOfSpeaks.sort()

    while None in listOfSpeaks:
        listOfSpeaks.remove(None)

    listOfSpeaks=listOfSpeaks[2:-2]
    double_adj_speaks = 0
    for s in listOfSpeaks:
        double_adj_speaks+=float(s)
    if had_bye(t) == True:
        double_adj_speaks+= avg_team_speaks(t)*(num_byes(t)+num_forfeit_wins(t))
    return double_adj_speaks

def double_adjusted_ranks(t):
    if TabSettings.objects.get(key = "cur_round").value < 5:
        return tot_ranks(t)
    elif TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t)+num_no_show(t)) < 5:
        return avg_team_ranks(t)*(TabSettings.objects.get(key = "cur_round").value-4)
    
    if TabSettings.objects.get(key = "cur_round").value > TabSettings.objects.get(key = "tot_rounds").value:
        num_rounds = TabSettings.objects.get(key = "tot_rounds").value
    else:
        num_rounds = TabSettings.objects.get(key = "cur_round").value
    list_of_ranks = []
    for i in range(num_rounds):
        list_of_ranks += [None]

    for d in list(t.debaters.all()):
        debStats = RoundStats.objects.filter(debater = d)
        for r in debStats:
            if list_of_ranks[r.round.round_number-1] == None:
                if won_by_forfeit(r.round, t):
                    pass
                elif forfeited_round(r.round,t):
                    list_of_ranks[r.round.round_number-1] = 3.5
                else:
                    list_of_ranks[r.round.round_number-1] = r.ranks
            else:
                if won_by_forfeit(r.round, t):
                    pass
                elif forfeited_round(r.round,t):
                    list_of_ranks[r.round.round_number-1] += 3.5
                else:
                    list_of_ranks[r.round.round_number-1] += r.ranks

    for n in list(NoShow.objects.filter(no_show_team=t)):
        list_of_ranks[n.round_number-1] = 7
            
    list_of_ranks.sort()
    
    while None in list_of_ranks:
        list_of_ranks.remove(None)

    list_of_ranks=list_of_ranks[2:-2]
    double_adj_ranks = 0
    for r in list_of_ranks:
        double_adj_ranks+=float(r)
    if had_bye(t) == True:
        double_adj_ranks+= avg_team_ranks(t)*(num_byes(t)+num_forfeit_wins(t))
    return double_adj_ranks

def opp_strength(t):
    opp_record = 0
    myGovRounds = Round.objects.filter(gov_team = t)
    myOppRounds = Round.objects.filter(opp_team = t)
    for r in myGovRounds:
        opp_record +=tot_wins(r.opp_team)
    for r in myOppRounds:
        opp_record +=tot_wins(r.gov_team)
    return opp_record
    
# Return a list of all teams who have no varsity members 
def all_nov_teams():
    return list(Team.objects.exclude(debaters__novice_status__exact=Debater.VARSITY))

# Return a list of all teams in the Database
def all_teams():
    return list(Team.objects.all())

#return tuples with pairs for varsity break
def tab_var_break():
    teams = rank_teams()
    the_break = teams[0:TabSettings.objects.get(key = "var_teams_to_break").value]               
    pairings = []
    for i in range(len(the_break)):
        pairings += [(the_break[i],the_break[len(the_break)-i-1])]
    return pairings                      


#return tuples with pairs for novice break
def tab_nov_break():
    novice_teams = rank_nov_teams()
    nov_break = novice_teams[0:TabSettings.objects.get(key = "nov_teams_to_break").value]
    pairings = []
    for i in range(len(nov_break)):
        pairings += [(nov_break[i],nov_break[len(nov_break)-i-1])]
    return pairings


# Returns a tuple b
def team_score(team):
    return (-tot_wins(team),
            -tot_speaks(team),
            tot_ranks(team),
            -single_adjusted_speaks(team),
            single_adjusted_ranks,
            -double_adjusted_speaks(team),
            double_adjusted_ranks,
            -opp_strength(team))


def team_score_except_record(team):
    return team_score(team)[1:]


def rank_teams():
    return sorted(all_teams(), key=team_score)


def rank_teams_except_record(teams):
    return sorted(all_teams(), key=team_score_except_record)


def rank_nov_teams():
    return sorted(all_nov_teams(), key=team_score)


def rank_nov_speakers():
    debs = list(Debater.objects.filter(novice_status=1))
    random.shuffle(debs, random = random.random)
    speakers = sorted(debs, key=lambda d: double_adjusted_ranks_deb(d))
    speakers = sorted(speakers, key=lambda d: double_adjusted_speaks_deb(d), reverse = True)
    speakers = sorted(speakers, key=lambda d: tot_ranks_deb(d))
    speakers = sorted(speakers, key=lambda d: tot_speaks_deb(d), reverse = True)
    return speakers

def avg_deb_speaks(d):
    tot_speak = 0
    #This is all the rounds the debater debated in
    my_rounds = RoundStats.objects.filter(debater = d)
    for i in range(TabSettings.objects.get(key = "cur_round").value-1):
        tempSpeak = []
        for r in my_rounds:
            if r.round.round_number == i+1:
                tempSpeak += [r.speaks]
        if len(tempSpeak) != 0:
            tot_speak += float(sum(tempSpeak))/float(len(tempSpeak))

    t = deb_team(d)
    if TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1 == 0:
        return 0
    else:
        return float(tot_speak)/float(TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1)
                    
def avg_deb_ranks(d):
    tot_rank = 0
    t = deb_team(d)
    my_rounds = RoundStats.objects.filter(debater = d)
    for i in range(TabSettings.objects.get(key = "cur_round").value-1):
        temp_rank = []
        for r in my_rounds:
            if r.round.round_number == i+1:
                if forfeited_round(r.round,t):
                    temp_rank += [3.5]
                elif won_by_forfeit(r.round,t):
                    pass
                else:
                    temp_rank+=[r.ranks]
        if len(temp_rank) != 0:
            tot_rank += float(sum(temp_rank))/float(len(temp_rank))

                
    for n in list(NoShow.objects.filter(no_show_team=t)):
        tot_rank += 3.5

    if TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1 == 0:
        return 0
    else:
        #print d
        #print "tot ranks = " + str(tot_rank)
        #print TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1
        return float(tot_rank)/float(TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1)

    
#calculate the total speaks for the debater (if iron-manned, average that round)
def tot_speaks_deb(debater):
    tot_speak = 0
    #This is all the rounds the debater debated in
    my_rounds = debater.roundstats_set.all()
    for i in range(TabSettings.objects.get(key = "cur_round").value-1):
        temp_speak = []
        for r in my_rounds:
            if r.round.round_number == i+1:
                temp_speak += [r.speaks]
        if len(temp_speak) != 0:
            tot_speak += float(sum(temp_speak))/float(len(temp_speak))
    t = deb_team(debater)
    #If they had the bye or won by forfeit, need to add that round
    tot_speak += avg_deb_speaks(debater)*(num_byes(t)+num_forfeit_wins(t))
    return tot_speak

#calculate the total ranks for the debater (if iron-manned, average that round)
def tot_ranks_deb(d):
    tot_rank = 0
    t = deb_team(d)
    my_rounds = d.roundstats_set.all()
    for i in range(TabSettings.objects.get(key = "cur_round").value-1):
        temp_rank = []
        for r in my_rounds:
            if r.round.round_number == i+1:
                if forfeited_round(r.round,t):
                    temp_rank += [3.5]
                elif won_by_forfeit(r.round,t):
                    pass
                else:
                    temp_rank+=[r.ranks]
        if len(temp_rank) != 0:
            tot_rank += float(sum(temp_rank))/float(len(temp_rank))
            
    t=deb_team(d)
    for n in list(NoShow.objects.filter(no_show_team=t)):
        tot_rank += 3.5
    #If they had the bye, need to add that round
    tot_rank += avg_deb_ranks(d) * (num_byes(t)+num_forfeit_wins(t))
        
    return tot_rank



def single_adjusted_speaks_deb(debater):
    team = deb_team(debater)
    current_round = TabSettings.objects.get(key="cur_round").value
    total_rounds = TabSettings.objects.get(key="tot_rounds").value
    if current_round < 3:
        return tot_speaks_deb(debater)
    elif current_round - (num_byes(team) + num_forfeit_wins(team) + num_no_show(team)) < 3 :
        return avg_deb_speaks(debater)*(current_round-2)

    if current_round > total_rounds:
        num_rounds = total_rounds
    else:
        num_rounds = current_round
    listOfSpeaks = []
    for i in range(num_rounds):
        listOfSpeaks += [[None]]
    deb_stats = debater.roundstats_set.all()
    for r in deb_stats:
        if listOfSpeaks[r.round.round_number-1] == [None]:
            listOfSpeaks[r.round.round_number-1] = [r.speaks]
        else:
            listOfSpeaks[r.round.round_number-1] += [r.speaks]
    while [None] in listOfSpeaks:
        listOfSpeaks.remove([None])
    for i in range(len(listOfSpeaks)):
        listOfSpeaks[i] = float(sum(listOfSpeaks[i]))/float(len(listOfSpeaks[i]))
        
    listOfSpeaks.sort()
    listOfSpeaks=listOfSpeaks[1:-1]
    sing_adj_speaks = sum(listOfSpeaks)
    sing_adj_speaks += avg_deb_speaks(debater)*(num_byes(team)+num_forfeit_wins(team))
    return sing_adj_speaks
                            

def single_adjusted_ranks_deb(d):
    t = deb_team(d)
    if TabSettings.objects.get(key = "cur_round").value < 3:
        return tot_ranks_deb(d)
    elif TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t)+num_no_show(t)) < 3 :
        return avg_deb_ranks(d)*(TabSettings.objects.get(key = "cur_round").value-2)
    
    if TabSettings.objects.get(key = "cur_round").value > TabSettings.objects.get(key = "tot_rounds").value:
        num_rounds = TabSettings.objects.get(key = "tot_rounds").value
    else:
        num_rounds = TabSettings.objects.get(key = "cur_round").value
    list_of_ranks = []
    for i in range(num_rounds):
        list_of_ranks += [[None]]
        
    debStats = RoundStats.objects.filter(debater = d)
    for r in debStats:
        if list_of_ranks[r.round.round_number-1] == [None]:
            if won_by_forfeit(r.round, t):
                pass
            elif forfeited_round(r.round,t):
                list_of_ranks[r.round.round_number-1] = [3.5]
            else:
                list_of_ranks[r.round.round_number-1] = [r.ranks]
        else:
            if won_by_forfeit(r.round, t):
                pass
            elif forfeited_round(r.round,t):
                list_of_ranks[r.round.round_number-1] += [3.5]
            else:
                list_of_ranks[r.round.round_number-1] +=[r.ranks]
    while [None] in list_of_ranks:
        list_of_ranks.remove([None])
    for i in range(len(list_of_ranks)):
        list_of_ranks[i] = float(sum(list_of_ranks[i]))/float(len(list_of_ranks[i]))
        
    for n in list(NoShow.objects.filter(no_show_team=t)):
        list_of_ranks[n.round_number-1] = 3.5
        
    list_of_ranks.sort()
    list_of_ranks=list_of_ranks[1:-1]
    sing_adj_ranks = 0
    for s in list_of_ranks:
        sing_adj_ranks+=s
    t = deb_team(d)
    avg_deb_ranks(d)*(num_byes(t)+num_forfeit_wins(t))
    return sing_adj_ranks

def double_adjusted_speaks_deb(d):
    t = deb_team(d)
    if TabSettings.objects.get(key = "cur_round").value < 5:
        return tot_speaks_deb(d)
    elif TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t)+num_no_show(t)) < 5:
        return avg_deb_speaks(d)*(TabSettings.objects.get(key = "cur_round").value-4)
    
    if TabSettings.objects.get(key = "cur_round").value > TabSettings.objects.get(key = "tot_rounds").value:
        num_rounds = TabSettings.objects.get(key = "tot_rounds").value
    else:
        num_rounds = TabSettings.objects.get(key = "cur_round").value
    listOfSpeaks = []
    for i in range(num_rounds):
        listOfSpeaks += [[None]]
    debStats = RoundStats.objects.filter(debater = d)
    for r in debStats:
        if listOfSpeaks[r.round.round_number-1] == [None]:
            listOfSpeaks[r.round.round_number-1] = [r.speaks]
        else:
            listOfSpeaks[r.round.round_number-1] += [r.speaks]
    while [None] in listOfSpeaks:
        listOfSpeaks.remove([None])
    for i in range(len(listOfSpeaks)):
        listOfSpeaks[i] = float(sum(listOfSpeaks[i]))/float(len(listOfSpeaks[i]))


    listOfSpeaks.sort()
    listOfSpeaks=listOfSpeaks[2:-2]
    double_adj_speaks = 0
    for s in listOfSpeaks:
        double_adj_speaks+=s
    t = deb_team(d)
    double_adj_speaks += avg_deb_speaks(d)*(num_byes(t)+num_forfeit_wins(t))
    return double_adj_speaks
                            

def double_adjusted_ranks_deb(d):
    t = deb_team(d)
    if TabSettings.objects.get(key = "cur_round").value < 5:
        return tot_ranks_deb(d)
    elif TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t)+num_no_show(t)) < 5:
        return avg_deb_ranks(d)*(TabSettings.objects.get(key = "cur_round").value-4)
    
    if TabSettings.objects.get(key = "cur_round").value > TabSettings.objects.get(key = "tot_rounds").value:
        num_rounds = TabSettings.objects.get(key = "tot_rounds").value
    else:
        num_rounds = TabSettings.objects.get(key = "cur_round").value
    list_of_ranks = []
    for i in range(num_rounds):
        list_of_ranks += [[None]]
    debStats = d.roundstats_set.all()
    for r in debStats:
        #print d
        #print list_of_ranks
        #print list_of_ranks[r.round.round_number-1]
        if list_of_ranks[r.round.round_number-1] == [None]:
            #print "in None"
            if won_by_forfeit(r.round, t):
                pass
            elif forfeited_round(r.round,t):
                list_of_ranks[r.round.round_number-1] = [3.5]
            else:
                list_of_ranks[r.round.round_number-1] = [r.ranks]
        else:
            #print "list_of_ranks of " + str(d)
            #print list_of_ranks
            if won_by_forfeit(r.round, t):
                pass
            elif forfeited_round(r.round,t):
                list_of_ranks[r.round.round_number-1] += [3.5]
            else:
                list_of_ranks[r.round.round_number-1] += [r.ranks]
    while [None] in list_of_ranks:
        list_of_ranks.remove([None])
    for i in range(len(list_of_ranks)):
        list_of_ranks[i] = float(sum(list_of_ranks[i]))/float(len(list_of_ranks[i]))

    for n in list(NoShow.objects.filter(no_show_team=t)):
        list_of_ranks[n.round_number-1] = 3.5
    list_of_ranks.sort()
    list_of_ranks=list_of_ranks[2:-2]
    double_adj_ranks = 0
    for s in list_of_ranks:
        double_adj_ranks+=s
    t = deb_team(d)
    double_adj_ranks += avg_deb_ranks(d)*(num_byes(t)+num_forfeit_wins(t))
    return double_adj_ranks
                      
def deb_team(d):
    return d.team_set.all()[0]

# Returns a tuple used for comparing two debaters 
# in terms of their overall standing in the tournament
def debater_score(debater):
    return (-tot_speaks_deb(debater),
            tot_ranks_deb(debater),
            -single_adjusted_speaks_deb(debater),
            single_adjusted_ranks_deb(debater),
            -double_adjusted_speaks_deb(debater),
            double_adjusted_ranks_deb(debater))

def rank_speakers():
    return sorted(Debater.objects.all(), key=debater_score)

