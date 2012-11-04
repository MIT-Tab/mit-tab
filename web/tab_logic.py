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

#Always sort so that top team is at the beginning of the list - use reverse!

from tab.models import *
from django.db.models import *

import random
import pairing_alg
import assign_judges
import errors
from decimal import *
import send_texts
from datetime import datetime
import pprint

from cache_logic import cache
    
#will return false if not ready to pair yet
def pair_round():
    """ 
    Pair the next round of debate.
    This function will do the following:
        1) Check that we can pair the round
        2) Check that we have scratches all judges from
           teams of the same school, and if not add these 
           scratches
        3) Record no-show teams
        4) Setup the list of teams by either seed or speaks
        5) Calculate byes
        6) Calculate pull ups based on byes
        7) Pass in evened brackets to the perfect pairing algorithm
        8) Assign judges to pairings
        9) Assign rooms to pairings
    
    pairings are computed in the following format: [gov,opp,judge,room]
    and then directly put into the database
    FIXME: Allow for good rollback behavior
    """
    #pairings = [gov,opp,judge,room]
    current_round = TabSettings.objects.get(key="cur_round").value
    try:
        ready_to_pair(current_round)
    except errors.NotEnoughJudgesError:
        raise errors.NotEnoughJudgesError()
    except errors.NotEnoughRoomsError:
        raise errors.NotEnoughRoomsError()
    except errors.PrevRoundNotEnteredError:
        raise errors.PrevRoundNotEnteredError
    
    # For testing purposes
    random.seed(0xBEEF)
    
    # add scratches for teams/judges from the same school 
    # NOTE that this only happens if they haven't already been added
    add_scratches_for_school_affil()

    list_of_teams = [None]*current_round
    pull_up = None

    #Record no-shows
    forfeit_teams = list(Team.objects.filter(checked_in=False))
    for t in forfeit_teams:
        n = NoShow(no_show_team = t, round_number = current_round)
        n.save()
    
    # If it is the first round, pair by *seed*
    if current_round == 1:
        list_of_teams = list(Team.objects.filter(checked_in=True))

        #If there are an odd number of teams, give a random team the bye
        if len(list_of_teams) % 2 == 1:
            b = Bye(bye_team = list_of_teams[random.randint(0,len(list_of_teams)-1)], round_number = current_round)
            b.save()
            list_of_teams.remove(b.bye_team)

        #Sort the teams by seed.
        list_of_teams = sorted(list_of_teams, key=lambda team: team.seed, reverse = True)

        #pairings = [None]* Team.objects.count()/2
    # Otherwise, pair by *speaks*
    else:
        bye_teams = [bye.bye_team for bye in Bye.objects.all()]
        # For each time that a team has won by forfeit, add them
        # to the list of bye_teams
        bye_teams = bye_teams + team_wins_by_forfeit()
        # FIXME (jolynch): Why is this random thing here? - (julia) If there are multiple teams that have had the bye/won by forfeit,
        #we want to order that they are inserted into the middle of the bracket to be random.  I need to change the code below so
        #that this is actually true/used - See issue 3
        random.shuffle(bye_teams, random= random.random)
        
        # Bucket all the teams into brackets
        # NOTE: We do not bucket teams that have only won by
        #       forfeit/bye in every round because they have no speaks
        all_checked_in_teams = Team.objects.filter(checked_in=True)
        team_buckets = [(tot_wins(team), team)
                        for team in all_checked_in_teams
                        if current_round - bye_teams.count(team) != 1]
        list_of_teams = [rank_teams_except_record([team 
                                                  for (w,team) in team_buckets 
                                                  if w == i])
                         for i in range(current_round)]

        # Take care of teams that only have forfeits/byes
        # FIXME (julia): This should just look at the bye teams. No need to look at all teams, plus looking only at bye teams will
        #insert them in a random order. See issue 3
        if len(bye_teams) != 0:
            for t in list(Team.objects.filter(checked_in=True)):
                # pair into the middle
                if current_round-bye_teams.count(t) == 1: 
                    print t
                    list_of_teams[current_round-1].insert(int(float(len(list_of_teams[tot_wins(t)]))/2.0),t)
        print "these are the teams before pullups"
        print pprint.pprint(list_of_teams)
        
        # Correct for brackets with odd numbers of teams
        #  1) If we are in the bottom bracket, give someone a bye
        #  2) If we are in 1-up bracket and there are no all down
        #     teams, give someone a bye
        #  FIXME: Do we need to do special logic for smaller brackets? - (julia) I need to make the logic more general to deal
        # with if there are no teams in the all down or up one bracket. See Issue 4
        #  3) Otherwise, find a pull up from the next bracket 
        for bracket in reversed(range(current_round)):
            if len(list_of_teams[bracket]) % 2 != 0:
                #print "need pull-up"
                # If there are no teams all down, give the bye to a one down team.
                if bracket == 0: 
                    byeint = len(list_of_teams[bracket])-1
                    b = Bye(bye_team = list_of_teams[bracket][byeint],
                            round_number = current_round)
                    b.save()
                    list_of_teams[bracket].remove(list_of_teams[bracket][byeint])
                elif bracket == 1 and len(list_of_teams[0]) == 0: #in 1 up and no all down teams
                    found_bye = False
                    for byeint in range(len(list_of_teams[1])-1, -1, -1):
                        if had_bye(list_of_teams[1][byeint]):
                            pass
                        elif found_bye == False:
                            b = Bye(bye_team = list_of_teams[1][byeint], round_number = current_round)
                            b.save()
                            list_of_teams[1].remove(list_of_teams[1][byeint])
                            found_bye = True
                    if found_bye == False:
                        raise errors.NotEnoughTeamsError()
                else: 
                    pull_up = None
                    # FIXME (jolynch): Try to use descriptive variable names. (julia) - I'll fix this.
                    # instead of commenting

                    # i is the last team in the bracket below
                    i = len(list_of_teams[bracket-1])-1 
                    pullup_rounds = Round.objects.exclude(pullup=None)
                    teams_been_pulled_up = [r.gov_team for r in pullup_rounds if r.pullup == Round.GOV]
                    teams_been_pulled_up.extend([r.opp_team for r in pullup_rounds if r.pullup == Round.OPP])
                    #find the lowest team in bracket below that can be pulled up
                    while pull_up == None: 
                        if list_of_teams[bracket-1][i] not in teams_been_pulled_up:
                            pull_up = list_of_teams[bracket-1][i]
                            list_of_teams[bracket].append(pull_up)
                            list_of_teams[bracket-1].remove(pull_up)
                            #after adding pull-up to new bracket and deleting from old, sort again by speaks making sure to leave any first
                            #round bye in the correct spot
                            removed_teams = []
                            for t in list(Team.objects.filter(checked_in=True)):
                                #They have all wins and they haven't forfeited so they need to get paired in
                                if current_round-bye_teams.count(t) == 1 and tot_wins(t) == bracket:
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
    print pprint.pprint(list_of_teams)
    if current_round > 1:
        for i in range(len(list_of_teams)):
            print "Bracket %i has %i teams" % (i, len(list_of_teams[i]))
    
    # Pass in the prepared nodes to the perfect pairing logic
    # to get a pairing for the round
    pairings = []
    for bracket in range(current_round):
        if current_round == 1:
            temp = pairing_alg.perfect_pairing(list_of_teams)
        else:
            temp = pairing_alg.perfect_pairing(list_of_teams[bracket])
            print "Pairing round %i of size %i" % (bracket,len(temp))
        for pair in temp:
            pairings.append([pair[0],pair[1],[None],[None]])

    # FIXME: WHY DO WE RANDOMIZE THIS - we want the order of which fullseeded teams get the best judge to be random.
    # We should possibly also sort on the weakest team first? I.e. a fullseed/halfseed should get a better judge than a
    # fullseed/freeseed, etc. - Julia to fix. Issue 6.
    # should randomize first
    if current_round == 1:
        random.shuffle(pairings, random= random.random)
        pairings = sorted(pairings, key = lambda team: highest_seed(team[0],team[1]), reverse = True)
    # sort with pairing with highest ranked team first
    else: 
        sorted_teams = rank_teams()
        print sorted_teams
        print "pairings"
        print pairings
        pairings = sorted(pairings, key=lambda team: min(sorted_teams.index(team[0]), sorted_teams.index(team[1])))
        
    # assign judges
    pairings = assign_judges.add_judges(pairings)
    
    #assign rooms (does this need to be random? maybe bad to have top ranked teams/judges in top rooms?)
    rooms = Room.objects.all()
    rooms = sorted(rooms, key=lambda r: r.rank, reverse = True)

    for i in range(len(pairings)):
        pairings[i][3] = rooms[i]
    

    #enter into database
    for p in pairings:
        r = Round(round_number = current_round,
                  gov_team = p[0],
                  opp_team = p[1],
                  judge = p[2],
                  room = p[3])
        if p[0] == pull_up:
            r.pullup = Round.GOV
        elif p[1] == pull_up:
            r.pullup = Round.OPP
        r.save()

#This method is tested by testsUnitTests.all_ready_to_pair_tests()
def ready_to_pair(round_to_check):
    """
    Check if we can pair a round using basic heuristics 
    In particular:
        1) We need enough N/2 judges that are *checked in*
        2) Do we have N/2 rooms
        3) We need all rounds to be entered from the previous round
    """
    if CheckIn.objects.filter(round_number = round_to_check).count() < Team.objects.filter(checked_in=True).count()/2:
        raise errors.NotEnoughJudgesError()
    elif Room.objects.all().count() < Team.objects.filter(checked_in=True).count()/2:
        raise errors.NotEnoughRoomsError()
    elif round_to_check != 1 and RoundStats.objects.all().count() < Round.objects.filter(round_number = round_to_check-1).count()*4:
        raise errors.PrevRoundNotEnteredError()
    else:
        prev_rounds = Round.objects.filter(round_number = round_to_check-1)
        for r in prev_rounds:
            if r.victor == 0:
                raise errors.PrevRoundNotEnteredError()
    
    return True

#This method is tested by testsUnitTests.all_add_scratches_for_school_affil_tests()
def add_scratches_for_school_affil():
    """
    Add scratches for teams/judges from the same school
    Only do this if they haven't already been added
    """
    print "Creating judge-affiliation scratches ",datetime.now()
    all_judges = Judge.objects.all()
    all_teams = Team.objects.all()
    for judge in all_judges:
        for team in all_teams:
            if team.school in judge.schools.all():
                if Scratch.objects.filter(judge = judge, team = team).count() == 0:
                    Scratch.objects.create(judge = judge,team = team, scratch_type = 1)
    print "Done creating judge-affiliation scratches ", datetime.now()

#This method is tested by testsUnitTests.all_highest_seed()
def highest_seed(team1,team2):
    if team1.seed > team2.seed:
        return team1.seed
    else:
        return team2.seed
    
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
        if a.pullup == Round.OPP:
            return True
    for a in list(Round.objects.filter(opp_team = t)):
        if a.pullup == Round.GOV:
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

def num_forfeit_wins(team):
    forfeit_wins = Round.objects.filter(
            Q(gov_team=team, victor=Round.GOV_VIA_FORFEIT)|
            Q(opp_team=team, victor=Round.OPP_VIA_FORFEIT)|
            Q(gov_team=team, victor=Round.ALL_WIN)|
            Q(opp_team=team, victor=Round.ALL_WIN)).count()
    return forfeit_wins

def num_no_show(t):
    return NoShow.objects.filter(no_show_team = t).count()

#Return true if the team forfeited the round, otherwise, return false
def forfeited_round(r,t):
    if Round.objects.filter(gov_team = t, round_number = r.round_number).count() > 0:
        if r.victor == Round.OPP_VIA_FORFEIT or r.victor == Round.ALL_DROP:
            return True
    elif Round.objects.filter(opp_team = t, round_number = r.round_number).count() > 0:
        if r.victor == Round.GOV_VIA_FORFEIT or r.victor == Round.ALL_DROP:
            return True
    else:
        return False


###Return true if the team won the round because the other team forfeited, otherwise return false
def won_by_forfeit(r,t):
    if Round.objects.filter(gov_team = t, round_number = r.round_number).count() > 0:
        if r.victor == Round.GOV_VIA_FORFEIT or r.victor == Round.ALL_WIN:
            return True
    elif Round.objects.filter(opp_team = t, round_number = r.round_number).count() > 0:
        if r.victor == Round.OPP_VIA_FORFEIT or r.victor == Round.ALL_WIN:
            return True
    else:
        return False
               
def team_wins_by_forfeit():
    """ 
    Finds teams that have won by forfeit.

    A team can win by forfeit either by having XXX_VIA_FORFEIT
    or by having ALL_WIN situations.

    Returns:
        wins_by_forfeit - A list of *teams* for each time they 
        have won by forfeit, there very well may be duplicates.
    """
    wins_by_forfeit = []
    for r in Round.objects.filter(victor = Round.GOV_VIA_FORFEIT):
        print r.round_number
        print str(r.gov_team) + " won via forfeit"
        wins_by_forfeit.append(r.gov_team)
    for r in Round.objects.filter(victor = Round.OPP_VIA_FORFEIT):
        print r.round_number
        print str(r.opp_team) + " won via forfeit"
        wins_by_forfeit.append(r.opp_team)
    for r in Round.objects.filter(victor = Round.ALL_WIN):
        print r.round_number
        print str(r.gov_team)+ ", " + str(r.opp_team) + " won via forfeit"
        wins_by_forfeit.append(r.gov_team)
        wins_by_forfeit.append(r.opp_team)
    return wins_by_forfeit

#Calculate the total number of wins a team has
def tot_wins(team):
    tot_wins = Round.objects.filter(
            Q(gov_team=team, victor=Round.GOV)|
            Q(opp_team=team, victor=Round.OPP)).count()
    # If a team had the bye, they won't have a round for that win so add one win
    tot_wins += num_byes(team) 
    # If a team has won by forfeit, we didn't count that yet
    tot_wins += num_forfeit_wins(team)
    return tot_wins

#Calculate the team's average speaks
def avg_team_speaks(t):
    tot_speaks = 0
    for d in list(t.debaters.all()):
        deb_stats = list(RoundStats.objects.filter(debater = d))
        for r in deb_stats:
            tot_speaks += r.speaks
            
    if TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1 == 0:
        return 0
    else:
        return float(tot_speaks)/float(TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t))-1)


#Calculate the team's average ranks
def avg_team_ranks(t):
    tot_ranks = 0
    for d in t.debaters.all():
        deb_stats = list(RoundStats.objects.filter(debater = d))
        for r in deb_stats:
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
@cache()
def tot_speaks(team):
    tot_speaks = sum([tot_speaks_deb(deb, False)
                      for deb in team.debaters.all()])
    return tot_speaks

@cache()
def tot_ranks(team):
    tot_ranks = sum([tot_ranks_deb(deb, False)
                     for deb in team.debaters.all()])    
    return tot_ranks
                          
@cache()                         
def single_adjusted_speaks(t):
    current_round = TabSettings.objects.get(key = "cur_round").value
    total_rounds = TabSettings.objects.get(key = "tot_rounds").value
    if current_round < 3:
        return tot_speaks(t)
    elif current_round-(num_byes(t)+num_forfeit_wins(t)+num_no_show(t)) < 3 :
        return avg_team_speaks(t)*(current_round-2)
    
    if current_round > total_rounds:
        num_rounds = total_rounds
    else:
        num_rounds = current_round
    list_of_speaks = []
    for i in range(num_rounds):
        list_of_speaks += [None]
        
    for d in list(t.debaters.all()):
        deb_stats = RoundStats.objects.filter(debater = d)
        for r in deb_stats:
            if list_of_speaks[r.round.round_number-1] == None:
                list_of_speaks[r.round.round_number-1] = r.speaks
            else:
                list_of_speaks[r.round.round_number-1] += r.speaks
            
    while None in list_of_speaks:
        list_of_speaks.remove(None)
        
    list_of_speaks.sort()

    list_of_speaks=list_of_speaks[1:-1]
    sing_adj_speaks = 0
    for s in list_of_speaks:
        sing_adj_speaks+=float(s)
    if had_bye(t) == True:
        sing_adj_speaks+= avg_team_speaks(t)*(num_byes(t)+num_forfeit_wins(t))
    return sing_adj_speaks

@cache()
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
        deb_stats = RoundStats.objects.filter(debater = d)
        for r in deb_stats:
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

@cache()
def double_adjusted_speaks(t):
    if TabSettings.objects.get(key = "cur_round").value < 5:
        return tot_speaks(t)
    elif TabSettings.objects.get(key = "cur_round").value-(num_byes(t)+num_forfeit_wins(t)+num_no_show(t)) < 5 :
        return avg_team_speaks(t)*(TabSettings.objects.get(key = "cur_round").value-4)

    if TabSettings.objects.get(key = "cur_round").value > TabSettings.objects.get(key = "tot_rounds").value:
        num_rounds = TabSettings.objects.get(key = "tot_rounds").value
    else:
        num_rounds = TabSettings.objects.get(key = "cur_round").value
    list_of_speaks = []
    for i in range(num_rounds):
        list_of_speaks += [None]

    for d in list(t.debaters.all()):
        deb_stats = RoundStats.objects.filter(debater = d)
        for r in deb_stats:
            if list_of_speaks[r.round.round_number-1] == None:
                list_of_speaks[r.round.round_number-1] = r.speaks
            else:
                list_of_speaks[r.round.round_number-1] += r.speaks
            
    list_of_speaks.sort()

    while None in list_of_speaks:
        list_of_speaks.remove(None)

    list_of_speaks=list_of_speaks[2:-2]
    double_adj_speaks = 0
    for s in list_of_speaks:
        double_adj_speaks+=float(s)
    if had_bye(t) == True:
        double_adj_speaks+= avg_team_speaks(t)*(num_byes(t)+num_forfeit_wins(t))
    return double_adj_speaks

@cache()
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
        deb_stats = RoundStats.objects.filter(debater = d)
        for r in deb_stats:
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
    score = (0,0,0,0,0,0,0,0)
    try:
        score = (-tot_wins(team),
                 -tot_speaks(team),
                  tot_ranks(team),
                 -single_adjusted_speaks(team),
                  single_adjusted_ranks,
                 -double_adjusted_speaks(team),
                  double_adjusted_ranks,
                 -opp_strength(team))
    except Exception as e:
        print "Could not calculate team score for {}".format(team)
    return score

def team_score_except_record(team):
    return team_score(team)[1:]

def rank_teams():
    return sorted(all_teams(), key=team_score)

def rank_teams_except_record(teams):
    return sorted(teams, key=team_score_except_record)

def rank_nov_teams():
    return sorted(all_nov_teams(), key=team_score)

@cache()
def avg_deb_speaks(d):
    tot_speak = 0
    #This is all the rounds the debater debated in
    current_round = TabSettings.objects.get(key = "cur_round").value
    my_rounds = RoundStats.objects.filter(debater = d)
    for i in range(current_round-1):
        tempSpeak = []
        for r in my_rounds:
            if r.round.round_number == i+1:
                tempSpeak += [r.speaks]
        if len(tempSpeak) != 0:
            tot_speak += float(sum(tempSpeak))/float(len(tempSpeak))

    t = deb_team(d)
    
    offset = 1
    if my_rounds.filter(round__round_number=(current_round-1)).count() == 0:
        offset = 2
    
    if current_round - (num_byes(t)+num_forfeit_wins(t)) - offset <= 0:
        return 0
    else:
        return float(tot_speak)/float(current_round-(num_byes(t)+num_forfeit_wins(t)) - offset)
        
@cache()
def avg_deb_ranks(d):
    tot_rank = 0
    t = deb_team(d)
    my_rounds = RoundStats.objects.filter(debater = d)
    current_round = TabSettings.objects.get(key = 'cur_round').value
    for i in range(current_round - 1):
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

    offset = 1
    if my_rounds.filter(round__round_number=(current_round-1)).count() == 0:
        offset = 2

    if current_round-(num_byes(t)+num_forfeit_wins(t)) - offset <= 0:
        return 0
    else:
        return float(tot_rank)/float(current_round-(num_byes(t)+num_forfeit_wins(t)) - offset)

    
# Calculate the total speaks for the debater (if iron-manned, average that round)
@cache()
def tot_speaks_deb(debater, average=True):
    tot_speak = 0
    #This is all the rounds the debater debated in
    my_rounds = debater.roundstats_set.all()
    current_round = TabSettings.objects.get(key = "cur_round").value
    for i in range(current_round - 1):
        temp_speak = []
        for r in my_rounds:
            if r.round.round_number == i+1:
                # This is why forfeit wins really need to be 0
                temp_speak += [r.speaks]
        if len(temp_speak) != 0:
            if average:
                tot_speak += float(sum(temp_speak))/float(len(temp_speak))
            else:
                tot_speak += float(sum(temp_speak))
    t = deb_team(debater)
    #If they had the bye or won by forfeit, need to add that round
    tot_speak += avg_deb_speaks(debater)*(num_byes(t)+num_forfeit_wins(t))
    return tot_speak

# Calculate the total ranks for the debater (if iron-manned, average that round)
@cache()
def tot_ranks_deb(d, average=True):
    tot_rank = 0
    t = deb_team(d)
    my_rounds = d.roundstats_set.all()
    current_round = TabSettings.objects.get(key = "cur_round").value
    for i in range(current_round - 1):
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
            if average:
                tot_rank += float(sum(temp_rank))/float(len(temp_rank))
            else:
                tot_rank += float(sum(temp_rank))
            
    t=deb_team(d)
    for n in list(NoShow.objects.filter(no_show_team=t)):
        tot_rank += 3.5
    #If they had the bye, need to add that round
    tot_rank += avg_deb_ranks(d) * (num_byes(t)+num_forfeit_wins(t))
        
    return tot_rank

@cache()
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
    list_of_speaks = []
    for i in range(num_rounds):
        list_of_speaks += [[None]]
    deb_stats = debater.roundstats_set.all()
    for r in deb_stats:
        if list_of_speaks[r.round.round_number-1] == [None]:
            list_of_speaks[r.round.round_number-1] = [r.speaks]
        else:
            list_of_speaks[r.round.round_number-1] += [r.speaks]
    while [None] in list_of_speaks:
        list_of_speaks.remove([None])
    for i in range(len(list_of_speaks)):
        list_of_speaks[i] = float(sum(list_of_speaks[i]))/float(len(list_of_speaks[i]))
        
    list_of_speaks.sort()
    list_of_speaks=list_of_speaks[1:-1]
    sing_adj_speaks = sum(list_of_speaks)
    sing_adj_speaks += avg_deb_speaks(debater)*(num_byes(team)+num_forfeit_wins(team))
    return sing_adj_speaks
                            
@cache()
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
        
    deb_stats = RoundStats.objects.filter(debater = d)
    for r in deb_stats:
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
        list_of_ranks.append(3.5)
        
    list_of_ranks.sort()
    list_of_ranks=list_of_ranks[1:-1]
    sing_adj_ranks = 0
    for s in list_of_ranks:
        sing_adj_ranks+=s
    t = deb_team(d)
    avg_deb_ranks(d)*(num_byes(t)+num_forfeit_wins(t))
    return sing_adj_ranks

@cache()
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
    list_of_speaks = []
    for i in range(num_rounds):
        list_of_speaks += [[None]]
    deb_stats = RoundStats.objects.filter(debater = d)
    for r in deb_stats:
        if list_of_speaks[r.round.round_number-1] == [None]:
            list_of_speaks[r.round.round_number-1] = [r.speaks]
        else:
            list_of_speaks[r.round.round_number-1] += [r.speaks]
    while [None] in list_of_speaks:
        list_of_speaks.remove([None])
    for i in range(len(list_of_speaks)):
        list_of_speaks[i] = float(sum(list_of_speaks[i]))/float(len(list_of_speaks[i]))


    list_of_speaks.sort()
    list_of_speaks=list_of_speaks[2:-2]
    double_adj_speaks = 0
    for s in list_of_speaks:
        double_adj_speaks+=s
    t = deb_team(d)
    double_adj_speaks += avg_deb_speaks(d)*(num_byes(t)+num_forfeit_wins(t))
    return double_adj_speaks
                            
@cache()
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
    deb_stats = d.roundstats_set.all()
    for r in deb_stats:
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
    try:
        return d.team_set.all()[0]
    except:
        return None
# Returns a tuple used for comparing two debaters 
# in terms of their overall standing in the tournament
def debater_score(debater):
    score = (0,0,0,0,0,0)
    try:
        score = (-tot_speaks_deb(debater),
                  tot_ranks_deb(debater),
                 -single_adjusted_speaks_deb(debater),
                  single_adjusted_ranks_deb(debater),
                 -double_adjusted_speaks_deb(debater),
                  double_adjusted_ranks_deb(debater))
    except Exception as e:
        print "Could not calculate debater score for {}".format(debater)
    print "finished scoring {}".format(debater)
    return score

def rank_speakers():
    return sorted(Debater.objects.all(), key=debater_score)

def rank_nov_speakers():
    return sorted(Debater.objects.filter(novice_status=1), key=debater_score)

