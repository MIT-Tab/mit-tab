# APDA results imports

Results publication is automatic. Legacy manual APDA standings publication flags
no longer control API access, and neither the Public Rankings Control panel nor
the board dashboard offers those switches. Speaker results become available once
outrounds are posted, or three hours after the final inround is complete.

`GET /api/live-results` remains the lightweight standings endpoint and never
includes the round bundle.

`GET /api/import-results` returns one complete snapshot for black-rod import and
its 12-hour cache task. The snapshot includes the live tournament metadata and
results plus the existing result/identity/count lists under their legacy keys:

- `varsity_team_placements`, `novice_team_placements`, `non_placing_teams`
- `varsity_speaker_awards`, `novice_speaker_awards`
- `new_debater_data`, `new_schools`, `debater_counts`
- `import_ready` and `bundle`

Before automatic publication, `import_ready` is false, speaker award lists are
empty, and `bundle` is null. After publication the bundle uses the existing
`mit_tab_black_rod_bundle` schema, including public judge names, chairs, rounds,
and speaks/ranks; it contains no scratch, judge-calibration, or private contact
data. The downloadable bundle remains available with a deprecation note.
Existing small API endpoints remain available for compatibility.

Deploy this endpoint before the sister black-rod PR that consumes it. No database
migration is needed, and existing manual publication flags can remain in storage.
