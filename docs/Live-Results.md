# Live results API

`GET /api/live-results` is public and returns schema version 1. Black Rod uses
these results to overlay provisional APDA points without importing final results.
The payload is cached for up to 60 seconds; consumers may cache it further.

Top-ten varsity and novice speaker results become available when the first
outround in either division is released using the pairing release control
(`var_teams_visible` / `nov_teams_visible`). Pairing an unreleased round or entering
its result does not release speakers. The separate Team Results publication
switch is not the outround release control. If no outrounds are released, speakers
become available three hours after all final-inround outcomes have timestamps.
No speaker scores or ballots are included.

Team results include one entry per team with its best guaranteed placement from
released rounds, even before any elimination result is entered. Quarterfinalists
have `place: 5`, semifinalists `place: 3`, finalists `place: 2`, and the champion
`place: 1`. A win improves the placement immediately; an opening partial-round bye
receives the next round's placement even before that round is paired. Unreleased
later pairings/results do not affect the response. Varsity and novice releases
are independent for team results.

Once a stage is released, its team IDs and placements are persisted in
`TabSettings` (`live_results_published_stages_0` / `_1`). Preparing challenge
picks, pairing another round, hiding pairings, or clearing caches does not erase
these already-public results or re-embargo speakers. Releasing the same stage
again replaces its published placements, so corrected outcomes can be reflected.
Snapshots contain no judge data or unpublished opponents. Team identities and
APDA IDs are serialized from current records so later identity corrections apply.
There is no database migration. Existing tournaments begin recording snapshots
when a public stage is next read or a release/pairing control is used; data hidden
before this version was deployed cannot be reconstructed from a snapshot.

The placement is a current points floor, not a prediction or a confirmed final
finish. Black Rod already converts each placement into the corresponding points
and recomputes its overlay on refresh, so advancing replaces a team's previous
provisional contribution rather than adding another contribution.
