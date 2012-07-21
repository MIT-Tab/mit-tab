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

import tab_logic
from tab.models import *
import mwmatching
import random
import errors

def addJudges(pairings):
    #Get all checkedInJudges
    checkedInObject = CheckIn.objects.filter(round_number = TabSettings.objects.get(key="cur_round").value)

    #assign judges - don't deal with the situation where all judges can't be paired in order
    judges = []
    for j in checkedInObject:
        judges += [j.judge]
        #sort judges with best judge first
        judges = sorted(judges, key=lambda j: j.rank, reverse = True)
    
    #assign judges
    assigned = 0
    foundPairing = False
    for i in range(len(judges)):
        #print "pairing"
        #print pairings
        #print i
        if assigned == len(pairings):
            foundPairing = True
            break
        else:
            for j in range(len(pairings)):
                #print pairings[j][2] == [None]
                if pairings[j][2] == [None] and judge_conflict(judges[i],pairings[j][0], pairings[j][1]) == False:
                    pairings[j][2] = judges[i]
                    assigned+=1
                    break
    #Now pairings should be assigned unless ran out of judges at end b/c of scratches
    if foundPairing == False:
        graph_edges = []
        for j in range(len(list(judges))):
            for p in range(len(pairings)):
                if judge_conflict(judges[j],pairings[p][0], pairings[p][1]) == False:
                    wt = calcWeight(p, j)
                    graph_edges +=[(p,j+len(pairings),wt)]
        
        judges_num = mwmatching.maxWeightMatching(graph_edges, maxcardinality=True)
        print "judges_num"
        print judges_num
        #if there is no possible assignment, raise an error
        #If the number of judges who should not be assigned a pairing isn't equal to the number of judges not assigned a pairing, then there is a problem
        if -1 in judges_num[0:len(pairings)] or len(graph_edges) == 0:
            raise errors.ToManyScratchesError()
        
        for i in range(len(pairings)):
            pairings[i][2] = judges[judges_num[i]-len(pairings)]
    return pairings


#take in a sorted list of judges and teams with the best judges and teams first
def calcWeight(judges, pair):
    wt = -1*(abs(judges-pair)**2)
    return wt

#return true if the judge is scratched from either team, false otherwise
def judge_conflict(j, team1, team2):
    if len(Scratch.objects.filter(judge = j).filter(team = team1)) != 0 or had_judge(j,team1) == True:
        #judge scratched from team one
        return True
    elif len(Scratch.objects.filter(judge = j).filter(team = team2)) != 0 or had_judge(j, team2) == True:
            #judge scratched from team two
            return True
    else:
        return False

#returns true if team has had judge before, otherwise false
def had_judge(j, t):
    if Round.objects.filter(gov_team = t, judge = j).count() != 0:
        return True
    elif Round.objects.filter(opp_team = t, judge = j).count() != 0:
        return True
    else:
        return False
    
    
    
