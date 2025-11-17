# Outrounds

Outrounds are now here!

## Handling the Break

When you reach the final in-round of your tournament, the button that is normally "Prepare Next Round" should read "Break 'em".  If this does not happen, please consult your `tot_rounds` TabSetting and ensure it is set properly.

The break page now includes a configuration card where you can choose how many varsity and novice teams should break along with which varsity round runs concurrently with the opening novice round. Adjust those values before pressing “Break ’em” and they’ll be saved automatically for future sessions.

All teams have a `break_preference` field which determines which break they'd prefer.  The only instance where this matters is if a novice team varsity breaks, but their break preference is set to novice, at which point they would not break varsity and only break to the novice bracket.

It will also perform a number of checks to ensure you have enough rooms and judges.  It does support paneled rounds (as long as they are consistently paneled) -- please consult the `nov_panel_size` and `var_panel_size` TabSettings for more information.  It will let you pair if you don't have enough judges, but it will warn you.

Once you’ve saved those break settings, MIT Tab will keep the novice and varsity brackets synchronized and automatically exclude any judges or rooms already committed to the simultaneous bracket.

## Managing Pairings

MIT Tab supports automatic judge assignment for outrounds. On the pairing view, you can click the "Assign Judges" button to automatically assign judges to rounds based on their rankings and scratches. This will respect panel sizes as configured in `var_panel_size` and `nov_panel_size`. The page also displays how many judges and rooms you need for the active round (and how many are currently available after excluding the concurrent bracket), so you can spot shortages before assigning panels.

Automatic room assignment for outrounds lives next to the judge assignment button. Click **Assign Rooms** whenever you want to reseed rooms; the concurrent-round exclusion happens automatically based on the break configuration.

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
