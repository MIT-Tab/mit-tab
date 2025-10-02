# Outrounds

Outrounds are now here!

## Handling the Break

When you reach the final in-round of your tournament, the button that is normally "Prepare Next Round" should read "Break 'em".  If this does not happen, please consult your `tot_rounds` TabSetting and ensure it is set properly.

This will then break the appropriate number of teams as determined by the `nov_teams_to_break` and `var_teams_to_break` TabSetting.

All teams have a `break_preference` field which determines which break they'd prefer.  The only instance where this matters is if a novice team varsity breaks, but their break preference is set to novice, at which point they would not break varsity and only break to the novice bracket.

It will also perform a number of checks to ensure you have enough rooms and judges.  It does support paneled rounds (as long as they are consistently paneled) -- please consult the `nov_panel_size` and `var_panel_size` TabSettings for more information.  It will let you pair if you don't have enough judges, but it will warn you.

The other tab setting that must be set correctly in order to ensure judges / rooms are not double booked is the `var_to_nov` variable.  If you would like varsity octafinals to happen at the same time as novice quarterfinals, this value should be `2` as the quotient of number of teams in varsity break rounds to the simultaneous novice break round is 2.  If they are happening at the same time, the value should be `1`, if they are octafinals at the same time as semifinals, then it should be 4, etc.

## Managing Pairings

MIT Tab supports automatic judge assignment for outrounds. On the pairing view, you can click the "Assign Judges" button to automatically assign judges to rounds based on their rankings and scratches (assuming you have set `var_to_nov` correctly - see above - AND all scratches are entered). This will respect panel sizes as configured in `var_panel_size` and `nov_panel_size`.

**Important**: Wing-only judges (judges with the "Wing Only" checkbox enabled) will be automatically excluded from chair assignments. They can still be assigned as panel members but will never be selected as the chair during automatic assignment.

The automatic judge assignment algorithm:
- Assigns the highest-ranked judges to the highest-seeded matchups
- Respects all scratches (both tab and team)
- Avoids rejudging when possible (if `allow_rejudges` is not enabled)
- Prioritizes non-wing-only judges for chair positions
- Creates backups before assignment for easy rollback

You can also manually assign judges using the dropdowns on the pairing view, which allows you to place judges very quickly if needed. When manually assigning:
- Click on any judge name to see alternative judge options
- You can set any judge as chair or remove them from the panel
- Wing-only judges will still appear in manual assignment options but are marked

Gov/opp assignment behavior depends on the `sidelock` TabSetting:
- If `sidelock` is set to `1`, gov/opp assignments will respect previous matchups to ensure teams don't switch sides when facing the same opponent, and sidelock status will be indicated on the pairing card and bolded on the front-facing pairing display.
- If `sidelock` is set to `0` (default), gov/opp will be assigned randomly, so be sure to change assignments according to whichever system your tournament has chosen to use.

If the `choice` TabSetting is enabled, the pairing card will indicate which team has choice (Gov or Opp) and this will be bolded on the front-facing pairing display. You can toggle choice assignments by clicking on the choice indicator in the pairing view.

Please also note the release pairings button that appears on the pairings page as normally appears.  This will toggle the visibility of that round's pairings.

## Viewing Pairings

MIT-Tab supports both list and bracket views for outround pairings. If the `show_outrounds_bracket` setting is enabled, users can switch between "List View" and "Bracket View" tabs on the outround pairings page. 

To enable bracket view, set `show_outrounds_bracket` to `1` in the Settings Form (Admin > Settings) or Admin Interface.

## Entering Results

Currently MIT Tab does not care about the type of decision (2-1, consensus, etc), but only the result.  Please enter these before advancing to the next out-round (this functions as it does for in rounds).
