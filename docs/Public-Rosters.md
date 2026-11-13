# Public roster history and tournament estimates

## When recording starts

The first publication of the **teams** list sets the earliest possible start of
public roster history for both teams and judges. Registration, if used, must also
be closed:

| Tournament setup | Recording begins |
| --- | --- |
| Teams list published while registration is open | When registration closes |
| Registration closes while teams list is private | When the teams list is first published |
| Registration is disabled or unconfigured | When the teams list is first published |

Either disabling new registrations or reaching their scheduled closing deadline
counts as closure. A scheduled future opening alone does not count as closure.
Reopening registration pauses new entries. Hiding and republishing a previously
published list preserves its original publication and history. Existing changes
are never backfilled. The initial migration records publication at upgrade time
for already-public tournaments because earlier publication timestamps are unknown.

Only roster additions/removals, debater membership changes, debater/judge names
and affiliations, team affiliations, and registered judge availability appear.
Team names, novice status, seeding, actual check-ins, calibration, scratches,
codes, contact information, and other private fields are excluded. Spreadsheet
and bulk roster imports create no public entries; their private audit records
retain their existing behavior. Import suppression is scoped to that import and
is restored even if it fails.

The public team and judge lists retain their own visibility settings. Entries
containing debater names are hidden whenever debater names are private. All public
timestamps use fixed EST (UTC−5), including in summer.

## Editing public entries

Full tournament admins can open **Setup → Public Change Log**, filter by team or
judge entries, and choose **Edit or delete** beside an entry. They can correct
the message, list, date/time in EST, and whether it contains debater names, or
delete the entry. Public lists refresh after either operation. This editor does
not add, modify, or delete immutable audit events. APDA Board accounts and limited
staff accounts cannot access it, even when a board user also has superuser flags.

## Calculator

**See expected records** on the public teams list opens the calculator. It uses
the selected checked-in or total team count and the configured inround count.
The page is private whenever the teams list is private and is not linked from
navigation.

Point values and record bracket estimates follow [APDA Calc](https://apda.online/calc/);
zero teams explicitly means zero points. Odd brackets alternate the extra
winner/loser from highest record downward, matching APDA Calc rounding.
