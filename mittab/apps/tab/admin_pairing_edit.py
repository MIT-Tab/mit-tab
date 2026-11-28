"""Sign staff pairing edits and validate elimination changes."""
import json

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.crypto import salted_hmac

from django.db import transaction

from mittab.apps.tab.admin_pairing_deletion import repair_public_placements
from mittab.apps.tab.board_operations import recompute_seeds
from mittab.apps.tab.live_results import published_team_placements
from mittab.libs.cacheing.public_cache import invalidate_outround_public_pairings_cache

from mittab.apps.tab.models import Outround, Round


def edit_revision(request, pairing):
    state = [
        pairing.gov_team_id, pairing.opp_team_id, pairing.chair_id, pairing.room_id, pairing.victor,
        list(pairing.judges.order_by("pk").values_list("pk", flat=True)),
    ]
    if isinstance(pairing, Round):
        state.extend((pairing.round_number, pairing.pullup, pairing.rfd))
    else:
        state.extend((pairing.type_of_round, pairing.num_teams, pairing.choice, pairing.sidelock))
    payload = (request.session.session_key, request.user.pk, request.path, pairing.pk, state)
    return salted_hmac(
        "mittab.admin.pairing.edit",
        json.dumps(payload, cls=DjangoJSONEncoder, sort_keys=True),
        algorithm="sha256",
    ).hexdigest()


def _winner_loser(victor, gov, opp):
    if victor in (Outround.GOV, Outround.GOV_VIA_FORFEIT):
        return gov, opp
    if victor in (Outround.OPP, Outround.OPP_VIA_FORFEIT):
        return opp, gov
    return None, None


def _validate_qualification(previous, proposed, peers):
    division, size, victor, gov, opp = proposed
    winner, loser = _winner_loser(victor, gov, opp)

    def advances(team, round_division, round_size):
        return team is not None and any(
            round_kind == round_division and stage < round_size and team in (government, opposition)
            for round_kind, stage, _, government, opposition in peers
        )

    if previous:
        old_division, old_size, old_victor, old_gov, old_opp = previous
        old_winner, _ = _winner_loser(old_victor, old_gov, old_opp)
        if (old_winner, old_division, old_size) != (winner, division, size) and advances(
            old_winner, old_division, old_size,
        ):
            raise ValidationError("Re-pair the later stage before changing its qualifying team.", code="pairing_conflict")
    if advances(loser, division, size):
        raise ValidationError("Re-pair the later stage before changing its qualifying team.", code="pairing_conflict")


def validate_elimination_edit(pairing, cleaned):
    names = ("type_of_round", "num_teams", "victor", "gov_team", "opp_team")
    if not all(name in cleaned for name in names):
        return
    division, size, victor, gov, opp = (cleaned[name] for name in names)
    proposed = (division, size, victor, gov.pk, opp.pk)
    previous = ((pairing.type_of_round, pairing.num_teams, pairing.victor, pairing.gov_team_id, pairing.opp_team_id)
                if pairing.pk else None)
    if (previous is None or previous[1] != size) and (not 2 <= size <= 512 or size & (size - 1)):
        raise ValidationError("Choose a stage size from 2 to 512 that is a power of two.", code="pairing_conflict")
    if previous == proposed:
        return
    divisions = [division, previous[0]] if previous else [division]
    peers = list(Outround.objects.filter(type_of_round__in=divisions).exclude(pk=pairing.pk)
                 .values_list("type_of_round", "num_teams", "victor", "gov_team_id", "opp_team_id"))
    _validate_qualification(previous, proposed, peers)

    entrants = {gov.pk, opp.pk}
    if previous and (previous[:2], previous[3:]) == (proposed[:2], proposed[3:]):
        return
    if len(entrants) != 2 or any(
        round_kind == division and (
            stage == size and entrants.intersection((government, opposition))
            or stage > size and _winner_loser(outcome, government, opposition)[1] in entrants
        )
        for round_kind, stage, outcome, government, opposition in peers
    ):
        raise ValidationError("Choose distinct teams that are still eligible for this stage.", code="pairing_conflict")


def repair_elimination_edit(actor, before, after):
    if before == after:
        return
    division = after["division"]
    bracket_fields = ("division", "num_teams", "gov_team", "opp_team")
    if before and any(before[field] != after[field] for field in bracket_fields):
        repair_public_placements(actor, [before])
        if before["division"] == division:
            return
    if before is None or before["victor"] != after["victor"] or before["division"] != division:
        recompute_seeds(division, actor)
    published_team_placements(division)
    transaction.on_commit(lambda: invalidate_outround_public_pairings_cache(division))
