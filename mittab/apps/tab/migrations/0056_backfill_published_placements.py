"""Preserve old public snapshots and recover released, manually created rounds.

Use historical models and explicit public fields only. Legacy settings remain
untouched for older deployments; application code no longer reads or writes them.
"""

import json

from django.db import migrations


def legacy_stages(raw, team_ids):
    try:
        stages = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(stages, dict):
        return {}
    result = {}
    for size, placements in stages.items():
        if not str(size).isdigit() or int(size) < 2 or not isinstance(placements, dict):
            continue
        result[int(size)] = {
            int(team_id): place for team_id, place in placements.items()
            if str(team_id).isdigit() and int(team_id) in team_ids
            and type(place) is int and place > 0
        }
    return result


def backfill_placements(apps, schema_editor):
    alias = schema_editor.connection.alias
    settings = apps.get_model("tab", "TabSettings").objects.using(alias)
    stages_model = apps.get_model("tab", "PublishedOutroundStage").objects.using(alias)
    placements_model = apps.get_model("tab", "PublishedTeamPlacement").objects.using(alias)
    team_ids = set(apps.get_model("tab", "Team").objects.using(alias).values_list(
        "pk", flat=True,
    ))
    for division, visibility_key in ((0, "var_teams_visible"), (1, "nov_teams_visible")):
        legacy = settings.filter(key=f"live_results_published_stages_{division}").first()
        stages = legacy_stages(legacy.value_string if legacy else None, team_ids)
        visibility = settings.filter(key=visibility_key).first()
        threshold = 256
        if visibility:
            try:
                threshold = int(visibility.value_string if visibility.value_string
                                is not None else visibility.value)
            except (TypeError, ValueError):
                pass
        # Match public_pages.released_outrounds; never infer publication from
        # completed outcomes or from the mere existence of a later round.
        rounds = list(apps.get_model("tab", "Outround").objects.using(alias).filter(
            type_of_round=division, num_teams__gte=threshold,
        ).values("num_teams", "gov_team_id", "opp_team_id", "victor"))
        opening_size = max(list(stages) + [row["num_teams"] for row in rounds], default=0)
        for size in {row["num_teams"] for row in rounds}:
            placements = {}
            for row in rounds:
                if row["num_teams"] != size:
                    continue
                winner_id = None
                if row["victor"] in (1, 3):
                    winner_id = row["gov_team_id"]
                elif row["victor"] in (2, 4):
                    winner_id = row["opp_team_id"]
                for team_id in (row["gov_team_id"], row["opp_team_id"]):
                    placements[team_id] = (
                        1 if size <= 2 else size // 4 + 1
                    ) if team_id == winner_id else size // 2 + 1
            if size == opening_size:
                bye_ids = apps.get_model("tab", "BreakingTeam").objects.using(alias).filter(
                    type_of_team=division,
                ).exclude(team_id__in=placements).values_list("team_id", flat=True)
                for team_id in bye_ids:
                    placements[team_id] = 1 if size <= 2 else size // 4 + 1
            stages[size] = placements
        for size, placements in stages.items():
            stage, _created = stages_model.get_or_create(
                type_of_round=division, num_teams=size,
            )
            placements_model.filter(stage_id=stage.pk).delete()
            placements_model.bulk_create([
                placements_model.model(stage_id=stage.pk, team_id=team_id, place=place)
                for team_id, place in placements.items()
            ])


class Migration(migrations.Migration):
    dependencies = [("tab", "0055_published_outround_stages")]

    operations = [migrations.RunPython(backfill_placements, migrations.RunPython.noop)]
