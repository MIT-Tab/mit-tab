# Live-results placement storage

Live results previously serialized every published outround stage into
`TabSettings.value_string`. Its 200-character database limit could be exceeded
by a single large break, or by adding later stages. MySQL then raised error 1406.
The same snapshot function runs during pairing, release, and the tournament
checklist, so the error also prevented normal semifinal and final pairing.

## Data model and publication

`PublishedOutroundStage` identifies a division and bracket size, with a unique
constraint on that pair. `PublishedTeamPlacement` stores one team foreign key
and integer placement per stage, with a unique constraint on stage/team.
Storage grows by rows instead of by a bounded settings value. Neither model
stores judges, scratches, calibration, eligibility, or pairing details.

Stages have no foreign key to individual debates: previously published placement
floors must survive hiding or rebuilding pairings. Team deletion removes that
team's placement rows. Existing public release thresholds determine which
current stages can refresh their snapshots; private later rounds cannot add
participants or improve placements. The opening stage still includes breaking
teams with byes. Winners, including forfeits, receive the next stage's floor.
The public API returns the minimum published placement for each team and keeps
its existing response schema and speaker embargo rules.

Each refresh locks the stage inside a transaction and replaces its placement
rows together. Unchanged snapshots do not rewrite placements. This prevents
concurrent refreshes from interleaving partial snapshots and permits corrections
to a released stage, including corrections that lower previously reported points.
The existing API response cache continues to limit refresh frequency.

## Deployment and recovery

Run the normal Django migrations before starting the new application version.
Migration 0055 creates the tables. Migration 0056 uses historical models to:

1. Import valid legacy snapshots, including stages whose pairings were hidden
   or deleted. Entries for deleted teams and malformed entries are ignored.
2. Rebuild currently public stages from existing debate participants and
   outcomes, replacing stale legacy values for those stages. This recovers
   published semifinals/finals created manually in admin, even when their
   snapshots could never be saved. Missing or corrupt JSON does not prevent
   recovery from the public rounds.
3. Leave pairings, outcomes, panels, chairs, and release settings untouched.
   Round existence or completed outcomes alone never imply publication.

The migration can be rerun without duplicating snapshots. Legacy settings are
left untouched as historical data; the new runtime never reads or writes them.
Reverting to older application code also reintroduces its storage limit; the
legacy values are not kept in sync with the new tables. Downgrading the schema
discards the new snapshots, so take a database backup before any downgrade.
Private rounds still require the normal release action. The migration cannot
reconstruct a deleted stage whose snapshot never saved and whose debates no
longer exist.

## Validation

Regression coverage includes a 64-team break released and paired through the
final, the checklist and live API, hidden/deleted stages, corrected results,
byes, forfeits, malformed legacy values, and actual migration execution. The
manual-round migration test checks that all pairing fields and panels survive.
Existing API tests enforce the public response schema and publication embargo.

The supplied production SQL backup was restored to an isolated MySQL database
and migrated successfully: six stages, 36 placement rows, and 20 public teams,
with both divisions' final placements recovered. All existing rounds, panels,
chairs, results, teams, judges, scratches, and audit rows were compared before
and after migration and the public API request and remained unchanged. The
private backup is not included in the repository.
