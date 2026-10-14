# Outrounds

Outrounds are now here!

## Handling the Break

When you reach the final in-round of your tournament, the button that is normally "Prepare Next Round" should read "Break 'em".  If this does not happen, please consult your `tot_rounds` TabSetting and ensure it is set properly.

This will then break the appropriate number of teams as determined by the `nov_teams_to_break` and `var_teams_to_break` TabSetting.

All teams have a `break_preference` field which determines which break they'd prefer.  The only instance where this matters is if a novice team varsity breaks, but their break preference is set to novice, at which point they would not break varsity and only break to the novice bracket.

It will also perform a number of checks to ensure you have enough rooms and judges.  It does support paneled rounds (as long as they are consistently paneled) -- please consult the `nov_panel_size` and `var_panel_size` TabSettings for more information.  It will let you pair if you don't have enough judges, but it will warn you.

## Managing Pairings

MIT Tab supports automatic judge and room assignment for outrounds. On the pairing view, use the Varsity and Novice dropdowns to choose which active outrounds are in scope, then click "Assign Judges" and/or "Assign Rooms". Assignment runs across the selected scope as a single pool, so judges and rooms are not double-booked within the selected rounds.

Automatic judge assignment respects panel sizes as configured in `var_panel_size` and `nov_panel_size`, and can use the `outround_judge_priority` setting when varsity and novice rounds are assigned together.

**Important**: Wing-only judges (judges with the "Wing Only" checkbox enabled) will be automatically excluded from chair assignments. They can still be assigned as panel members but will never be selected as the chair during automatic assignment.

The automatic judge assignment algorithm:
- Assigns the highest-ranked judges to the highest-seeded matchups
- Respects all scratches (both tab and team)
- Creates backups before assignment for easy rollback

You can also manually assign judges using the dropdowns on the pairing view, which allows you to place judges very quickly if needed. When manually assigning:
- Click on any judge name to see alternative judge options
- You can set any judge as chair or remove them from the panel
- Wing-only judges will still appear in manual assignment options but are marked

Please also note the release pairings button that appears on the pairings page as normally appears.  This will toggle the visibility of that round's pairings.

## Viewing Pairings

MIT-Tab supports both list and bracket views for outround pairings. If the `show_outrounds_bracket` setting is enabled, users can switch between "List View" and "Bracket View" tabs on the outround pairings page. 

To enable bracket view, check the `show_outrounds_bracket` setting in the Settings Form (Admin > Settings) or Admin Interface.

## Entering Results

Currently MIT Tab does not care about the type of decision (2-1, consensus, etc), but only the result.  Please enter these before advancing to the next out-round (this functions as it does for in rounds).
