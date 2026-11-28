"""Sign the records shown in a room deletion confirmation."""
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.utils.crypto import salted_hmac

from mittab.apps.tab.board_audit import pairing_snapshot, with_pairing_snapshot_relations
from mittab.apps.tab.models import (
    BreakingTeam, Outround, PublishedOutroundStage, PublishedTeamPlacement, TabSettings,
)


def room_deletion_scope(request, rooms, preview, counts):
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
        request.session.session_key, request.user.pk, request.path,
        list(rooms.order_by("pk").values_list("pk", "name", "rank")),
        preview, sorted((str(label), count) for label, count in counts.items()), pairings, list(stages.values_list("pk", "type_of_round", "num_teams")),
        list(placements), list(seeds), list(settings) if divisions else [],
    )
    return salted_hmac(
        "mittab.admin.room-deletion",
        json.dumps(state, cls=DjangoJSONEncoder, sort_keys=True),
        algorithm="sha256",
    ).hexdigest()
