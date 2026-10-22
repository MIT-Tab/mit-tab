"""Roster exports use exactly the corresponding import layout."""

from mittab.apps.tab.models import Judge, Room, Scratch, TabSettings, Team
from mittab.libs.spreadsheets import LAYOUTS


def roster_rows(kind):
    headers = list(LAYOUTS[kind])
    rows = []
    if kind == "teams":
        for team in (
            Team.objects.select_related("school", "hybrid_school")
            .prefetch_related("debaters")
            .order_by("name")
        ):
            row = [
                team.name,
                team.school.name,
                team.hybrid_school.name if team.hybrid_school else "",
                team.get_seed_display(),
            ]
            debaters = list(team.debaters.all())
            for index in range(2):
                if index < len(debaters):
                    debater = debaters[index]
                    row.extend(
                        [
                            debater.name,
                            debater.get_novice_status_display(),
                            debater.email or "",
                            debater.apda_id,
                        ]
                    )
                else:
                    row.extend(["", "", "", ""])
            rows.append(row)
    elif kind == "judges":
        judges = list(
            Judge.objects.prefetch_related("schools", "expected_checkins").order_by(
                "name"
            )
        )
        num_schools = max((len(judge.schools.all()) for judge in judges), default=0)
        num_rounds = TabSettings.get("tot_rounds", 5)
        headers += [f"School {i + 1}" for i in range(num_schools)]
        headers += [f"Round {i + 1} expected" for i in range(num_rounds)]
        for judge in judges:
            schools = [school.name for school in judge.schools.all()]
            expected = {
                checkin.round_number for checkin in judge.expected_checkins.all()
            }
            rows.append(
                [judge.name, judge.email or "", judge.rank]
                + schools
                + [""] * (num_schools - len(schools))
                + ["yes" if i + 1 in expected else "" for i in range(num_rounds)]
            )
    elif kind == "rooms":
        rows = list(Room.objects.order_by("name").values_list("name", "rank"))
    elif kind == "scratches":
        rows = [
            [
                scratch.team.name,
                scratch.judge.name,
                "team" if scratch.scratch_type == Scratch.TEAM_SCRATCH else "tab",
            ]
            for scratch in Scratch.objects.select_related("team", "judge").order_by(
                "pk"
            )
        ]
    return headers, rows
