"""Sign the records covered by an admin deletion confirmation."""
import json

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group, User
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.crypto import salted_hmac

from mittab.apps.tab.board_audit import pairing_snapshot, with_pairing_snapshot_relations
from mittab.apps.tab.models import (
    AuditEvent, BreakingTeam, Judge, ManualJudgeAssignment, Outround, PublishedOutroundStage,
    PublishedTeamPlacement, Scratch, TabSettings, Team, UserTournamentSetupPreference,
)


def _sign(request, selected, preview, counts, state):
    payload = (
        request.session.session_key, request.user.pk, request.path,
        selected.model._meta.label_lower, preview, sorted((str(label), count) for label, count in counts.items()),
        state,
    )
    return salted_hmac(
        "mittab.admin.deletion",
        json.dumps(payload, cls=DjangoJSONEncoder, sort_keys=True),
        algorithm="sha256",
    ).hexdigest()


def account_deletion_records(selected, confirmed):
    if selected.model is User:
        identity_field = "username"
        related_fields = (
            (User.groups.through, "user", ("group_id",)),
            (User.user_permissions.through, "user", ("permission_id",)),
            (LogEntry, "user", ()), (UserTournamentSetupPreference, "user", ()),
            (Judge, "created_by", ()), (Team, "created_by", ()), (Scratch, "created_by", ()),
            (ManualJudgeAssignment, "created_by", ()), (AuditEvent, "user", ()),
        )
    else:
        identity_field = "name"
        related_fields = (
            (Group.permissions.through, "group", ("permission_id",)),
            (User.groups.through, "group", ("user_id",)),
        )
    related = []
    for model, field, extra_fields in related_fields:
        rows = model._base_manager.using(selected.db).filter(**{f"{field}__in": selected}).order_by("pk")
        if confirmed:
            rows = rows.select_for_update()
        related.append(list(rows.values_list("pk", f"{field}_id", *extra_fields)))
    identity = list(selected.order_by("pk").values_list("pk", identity_field))
    return identity, related


def account_deletion_scope(request, selected, preview, counts, _confirmed):
    return _sign(request, selected, preview, counts, request.admin_account_deletion_records)


def room_deletion_scope(request, rooms, preview, counts, _confirmed):
    """Bind the visible cascade and the data used to repair the bracket."""
    database = rooms.db
    rounds = with_pairing_snapshot_relations(
        Outround.objects.using(database).filter(room__in=rooms).order_by("pk"),
    )
    pairings = [(round_obj.pk, pairing_snapshot(round_obj)) for round_obj in rounds]
    divisions = {snapshot["division"] for _, snapshot in pairings}

    stages = PublishedOutroundStage.objects.using(database).filter(type_of_round__in=divisions).order_by("pk")
    placements = (PublishedTeamPlacement.objects.using(database)
                  .filter(stage__in=stages).order_by("pk")
                  .values_list("pk", "stage_id", "team_id", "place"))
    seeds = (BreakingTeam.objects.using(database).filter(type_of_team__in=divisions)
             .order_by("pk").values_list("pk", "team_id", "seed", "effective_seed"))
    settings = (TabSettings.objects.using(database)
                .filter(key__in=("var_teams_visible", "nov_teams_visible"))
                .order_by("pk").values_list("key", "value", "value_string"))
    state = (
        list(rooms.order_by("pk").values_list("pk", "name", "rank")),
        pairings, list(stages.values_list("pk", "type_of_round", "num_teams")),
        list(placements), list(seeds), list(settings) if divisions else [],
    )
    return _sign(request, rooms, preview, counts, state)
