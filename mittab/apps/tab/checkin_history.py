"""Record and reverse changes made on Batch Check-In."""

from collections import defaultdict
from itertools import product

from django.db import transaction

from mittab.apps.tab.models import (
    BulkCheckInChange,
    CheckIn,
    Judge,
    JudgeExpectedCheckIn,
    Room,
    RoomCheckIn,
    Team,
)


ENTITY_MODELS = {
    "team": (Team, None, None),
    "judge": (Judge, CheckIn, "judge_id"),
    "judge_expected": (Judge, JudgeExpectedCheckIn, "judge_id"),
    "room": (Room, RoomCheckIn, "room_id"),
}


def latest_change(entity_type):
    return BulkCheckInChange.objects.filter(
        entity_type=entity_type, undone=False,
    ).first()


def lock_entities(entity_type, ids):
    entity_model, _, _ = ENTITY_MODELS[entity_type]
    return list(entity_model.objects.select_for_update().filter(
        pk__in=ids,
    ).order_by("pk").values_list("pk", flat=True))


def checked_cells(entity_type, ids, rounds):
    _, model, id_field = ENTITY_MODELS[entity_type]
    if model is None:
        return {
            (pk, None) for pk in Team.objects.filter(
                pk__in=ids, checked_in=True,
            ).values_list("pk", flat=True)
        }
    return set(model.objects.select_for_update().filter(
        **{f"{id_field}__in": ids}, round_number__in=rounds,
    ).values_list(id_field, "round_number"))


def write_cells(entity_type, cells, checked):
    _, model, id_field = ENTITY_MODELS[entity_type]
    if model is None:
        Team.objects.filter(pk__in=[pk for pk, _ in cells]).update(checked_in=checked)
    elif checked and model is JudgeExpectedCheckIn:
        # Save signals maintain the public roster's availability history.
        for pk, round_number in sorted(cells):
            model.objects.create(**{id_field: pk}, round_number=round_number)
    elif checked:
        model.objects.bulk_create([
            model(**{id_field: pk}, round_number=round_number)
            for pk, round_number in sorted(cells)
        ])
    else:
        ids_by_round = defaultdict(list)
        for pk, round_number in cells:
            ids_by_round[round_number].append(pk)
        for round_number, ids in sorted(ids_by_round.items()):
            model.objects.filter(
                **{f"{id_field}__in": ids}, round_number=round_number,
            ).delete()


@transaction.atomic
def apply_change(entity_type, ids, rounds, checked):
    ids = lock_entities(entity_type, ids)
    before = checked_cells(entity_type, ids, rounds)
    changed = set(product(ids, rounds)) - before if checked else before
    if changed:
        write_cells(entity_type, changed, checked)
        return BulkCheckInChange.objects.create(
            entity_type=entity_type, checked=checked, cells=sorted(changed),
        )
    return latest_change(entity_type)


@transaction.atomic
def undo_change(entity_type, change_id):
    change = latest_change(entity_type)
    if change is None or str(change.token) != change_id:
        return False

    cells = {tuple(cell) for cell in change.cells}
    ids = {pk for pk, _ in cells}
    if set(lock_entities(entity_type, ids)) != ids:
        return False

    # Recheck after acquiring the same locks used by Batch Check-In writes.
    latest = latest_change(entity_type)
    if latest is None or latest.pk != change.pk:
        return False
    current = checked_cells(entity_type, ids, {rnd for _, rnd in cells}) & cells
    if current != (cells if change.checked else set()):
        return False

    write_cells(entity_type, cells, not change.checked)
    change.undone = True
    change.save(update_fields=["undone"])
    return True
