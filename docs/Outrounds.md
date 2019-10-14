Outrounds
========

Outrounds are now here!

Handling the Break
------------------

When you reach the final in-round of your tournamet, the button that is normally "Prepare Next Round" should read "Break 'em".  If this does not happen, please consult your `tot_rounds` TabSetting and ensure it is set properly.

This will then break the appropriate number of teams as determined by the `nov_teams_to_break` and `var_teams_to_break` TabSetting.

It will also perform a number of checks to ensure you have enough rooms and judges.  It does support paneled rounds (as long as they are consistently paneled) -- please consult the `nov_panel_size` and `var_panel_setting` for more information.  It will let you pair if you don't have enough judges, but it will warn you.

The other tab setting that must be set correctly in order to ensure judges / rooms are not double booked is the `var_to_nov` variable.  If you would like varsity octafinals to happen at the same time as novice quarterfinals, this value should be `2` as the quotient of number of teams in varsity break rounds to the simultaneous novice break round is 2.  If they are happening at the same time, the value should be `1`, if they are octafinals at the same time as semifinals, then it should be 4, etc.

Managing Pairings
----------------

Currently MIT Tab does NOT support judge assignment, this must be done by hand.  However, assuming you have set `var_to_nov` correctly (see above) AND all scratches are entered, the dropdowns on the pairing view will allow you to place judges very quickly.  Furthermore, gov opp will be assigned randomly, so be sure to change that according to whichever type of system your tournament has chosen to use.

Please also not the release pairings button that appears on the pairings page as normally appears.  This will toggle the visibility of that round's pairings.

Entering Results
----------------

Currently MIT Tab does not care about the type of decision (2-1, consensus, etc), but only the result.  Please enter these before advancing to the next out-round (this functions as it does for in rounds).