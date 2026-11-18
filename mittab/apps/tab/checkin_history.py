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


class RestoreConflict(Exception):
    """The selected history no longer matches the check-in grid."""


@transaction.atomic
def restore_changes(entity_type, change_id, *, confirmed_head=None):
    history = BulkCheckInChange.objects.filter(entity_type=entity_type, undone=False)
    target = history.filter(token=change_id).first()
    if target is None:
        raise RestoreConflict

    changes = list(history.filter(pk__gte=target.pk))
    if not changes or changes[-1].pk != target.pk:
        raise RestoreConflict
    head = str(changes[0].token)
    if confirmed_head is not None and head != confirmed_head:
        raise RestoreConflict

    cells_by_change = [(change, {tuple(cell) for cell in change.cells}) for change in changes]
    affected = set().union(*(cells for _, cells in cells_by_change))
    ids = {pk for pk, _ in affected}
    if set(lock_entities(entity_type, ids)) != ids:
        raise RestoreConflict

    if list(history.filter(pk__gte=target.pk).values_list("pk", flat=True)) != [
        change.pk for change in changes
    ]:
        raise RestoreConflict

    current = checked_cells(entity_type, ids, {round_num for _, round_num in affected}) & affected
    restored = current.copy()
    for change, cells in cells_by_change:
        if restored & cells != (cells if change.checked else set()):
            raise RestoreConflict
        if change.checked:
            restored.difference_update(cells)
        else:
            restored.update(cells)

    check_in = restored - current
    check_out = current - restored
    if confirmed_head is not None:
        if check_in:
            write_cells(entity_type, check_in, True)
        if check_out:
            write_cells(entity_type, check_out, False)
        history.filter(pk__in=[change.pk for change in changes]).update(undone=True)

    return {
        "head": head,
        "before": target.created_at,
        "edits": len(changes),
        "check_in": len(check_in),
        "check_out": len(check_out),
    }


def undo_change(entity_type, change_id):
    change = latest_change(entity_type)
    if change is None or str(change.token) != change_id:
        return False
    try:
        restore_changes(entity_type, change.token, confirmed_head=change_id)
    except RestoreConflict:
        return False
    return True
