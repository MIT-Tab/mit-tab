Tab Policy
==========

You can find both a copy and paste tab policy for your tournament below, as
well as an in depth explanation of the tab policy.


Copy and Paste Tab Policy
-------------------------

```text
1. Round 1 will be paired randomly with power pairing for seeds. Hybrids may carry the free seed of either of their schools.

2. Rounds 2 through 5 will be power-protected within brackets: the top team in each by total cumulative speaker score will be paired against the bottom team, the second against the second-to-last, and so on as     much as possible subject to the following constraints (in decreasing order of precedence):
    a. Teams will not debate a team they have already debated
    b. Teams will not hit the pull-up more than once
    c. Teams will be protected from debating against other teams from their own school; hybrids may carry such protection from only the school against which they are protected. 
    d. Teams will not compete more than three times on the government side of a round.
    e. Teams will not compete more than four times on the opposition side of a round

3. Should a bracket contain an odd number of teams, a team will be pulled up from the bottom of the bracket below and paired within the higher bracket on the basis of speaker points. No team will be pulled up more than once. Should the bottom bracket contain an odd number of teams, the lowest speaking team will be given a bye. First round, a random team will receive the bye if necessary. A first round bye will be paired into the middle of the 1-0 bracket for round two.

4. Ironmen will be awarded a separate score for each of their speeches and will receive the average of these two scores for their overall total, and may receive speaker awards. A team which consisted of an ironman for one round may break if the partner was absent for an excellent reason; other teams which had an ironman round may not. Debaters who do not take part in all five rounds may not receive speaker awards.

5. Should a team fail to show up on time to the tournament, they will not be paired into the round and will receive a loss with speaks of zero and ranks of 7. Should they show up for the tournament, but fail to show up for a round, they will receive a loss with speaks of zero and ranks of 7 and their opponent will receive a win with average speaks and ranks. If it is first round, their opponent will be paired into the middle of the 1-0 bracket for round two.

6. After Round 5, the top eight teams will advance to the quarterfinal round. These teams will be determined by the following criteria (in order):
    I. Win-Loss Record
    II. Total speaker points
    III. Total ranks
    IV. Single-adjusted speaker points
    V. Single-adjusted ranks
    VI. Double-adjusted speaker points
    VII. Double-adjusted ranks
    VIII. Opposition Strength (Opposition wins / Number of opponents)
    IX. Computer-generated coin flip
```

Below is a comprehensive listing of MIT-TAB program's Tab Policy. Many of these
are configurable, especially the constraints within brackets, so if you need
something special check out the dedicated section in
[Advanced Topics](Advanced-Topics.md#modifying-the-pairing-algorithm)


Pairing
-------

### Pullups
* Pull up from the bottom of the next lowest bracket by win-loss ratio, and
  pair in by speaks.
* No team will be the pulled up more than once.
* No team will be pulled up more than one bracket.
* No team will debate the pullup more than once.

### Forfeits
* Winning teams get average speaks and ranks that are recalculated with every
round.
* Losing teams get speaks of 0 and ranks of 7.

### Byes
If a bye is needed to make the lowest bracket whole, a bye will be selected as
follows:
* In the first round the bye is selected randomly.
* In all preceding rounds the bye is selected as the lowest speaking team of
  the all down bracket.
* No team will get the bye more than once.
* Byes are tabbed as a win with debaters receiving average speaks.

### Bracketing
* Teams will only ever debate other teams of equivalent win-loss ratio, with
  the exception of the pullup.
* If a pullup is needed, the lowest speaking team in the bracket directly
  below is pulled up and paired in by speaks.
* If in the all-down bracket there is an odd number of teams, the lowest
  speaking team gets the Bye.  If this is the first round a random team gets
  the Bye.

MIT-TAB pairs from the top down, meaning that it will generate the top bracket,
make it whole, then generate the one own bracket, make it whole, etc ... This
has the following implications:
* Pull ups are chosen before the Bye
* There is no cross bracket optimization, we follow a strict set of rules to
  generate the brackets and then pair within those brackets.

### Pairing constraints within a bracket

MIT-TAB attempts to preserve the following constraints in order of severity.
The number in parenthesis is the default score if violated and the chosen
pairing is the minimized total score over all possible pairings.

1. You will not hit someone you have hit before. (penalty = 100,000)
2. You will not hit the pullup more than once. (penalty = 10,000)
3. You will not hit someone from your school. (penalty = 1,000)
4. No team shall have 4+ govs or 5+ opps. (penalty = 100 for 4+ govs,
   and 10 for 5+ opps)
5. Power pairing will be preserved as much as possible, in general high
   speaking teams will debate low speaking teams within the same bracket
   (penalty = the difference between the optimal power pairing positions of
   the teams in the pairing and where they actually are)

Judge Assignment
----------------

* In round 1, highest ranked judges are assigned to rounds in the
  following order:
  1. Rounds containing a full-seed
  2. Rounds containing a half-seed
  3. Rounds containing a free-seed
  4. Rounds containing only un-seeded teams
* In all other rounds, highest ranked judges are assigned to the rounds with
  the highest speaking teams, respecting scratches. Paneling is not currently
  supported.

Rankings
--------

### Speaker Ranking

1. Speaks [Who has higher average speaks]
2. Ranks [Who has lower average ranks]
3. Single adjusted speaks [Who has higher average speaks sans one high and low
   outlier]
4. Single adjusted ranks [Who has lower average ranks sans one high and low
   outliar] 
5. Double adjusted speaks [Who has higher median speaks]
6. Double adjusted ranks [Who has lower median ranks]

### Team Ranking

1. Team Speaks [Who has higher average speaks]
2. Team Ranks [Who has lower average ranks]
3. Team Single adjusted speaks [Who has higher average speaks sans one high
   and low outlier]
4. Team Single adjusted ranks [Who has lower average ranks sans one high and
   low outliar] 
5. Team Double adjusted speaks [Who has higher median speaks]
6. Team Double adjusted ranks [Who has lower median ranks] 
7. Opposition strength. [Average number of wins of opponents]
