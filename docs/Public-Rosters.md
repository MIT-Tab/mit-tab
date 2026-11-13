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

## Calculator and best/worst cases

**See expected records** on the public teams list opens the calculator. It uses
the selected checked-in or total team count and the configured inround count.
The two toggles preserve each other's selections. The page is private whenever
the teams list is private and is not linked from navigation.

Point values follow [APDA Calc](https://apda.online/calc/); zero teams explicitly
means zero points. The records table's **Best case** minimizes the final number
of teams with zero or one loss, and **Worst case** maximizes that number. These
are global bounds across all pullup results, rather than two fixed assumptions
about which side wins every pullup.

Always letting the higher-record team win does **not** generally minimize that
total. In a final-round pullup:

| Teams before the match | Higher-record team wins | Lower-record team wins |
| --- | --- | --- |
| One undefeated, one one-loss team | 1 team finishes with ≤1 loss | 2 teams finish with ≤1 loss |
| One one-loss, one two-loss team | 1 team finishes with ≤1 loss | 0 teams finish with ≤1 loss |

The first boundary favors a higher-record winner for minimizing the total; the
second favors a lower-record winner. Earlier choices also change later brackets.
For example, with 28 teams over five rounds, always choosing higher-record winners
leaves 6 down-zero/down-one teams; always choosing lower-record winners leaves 5.
The true minimum is 4 and the maximum is 6. For 53 teams the bounds are 9–11; for
117 teams they are 21–23.

The implementation enumerates possible pullup results round by round and merges
identical bracket distributions, whose future possibilities are identical. It
then selects a path attaining each final bound. Every table column belongs to
that same complete scenario; columns are not independently optimized. Equal-bound
ties use deterministic ordering. Within-bracket results split evenly and cannot
change bracket counts. Pairing proceeds from highest record downward; a pullup
comes from the next nonempty bracket. A final unmatched team receives a bye.
Draws, forfeits, extra byes, and pairing restrictions are not modeled.

Tests independently enumerate individual match winners for fields of 0–10 teams
and 1–6 rounds, verifying both bounds and that each displayed path is reachable.
Additional regression cases cover the supplied larger tournament sizes.
