import random
from django.db import transaction
from mittab.apps.tab.models import Outround, RoomCheckIn, Round, TabSettings
from mittab.libs import errors, mwmatching, tab_logic
from mittab.libs.assign_judges import (
    OutroundRoundPriority,
    sort_outround_pairings,
)


def add_rooms():
    room_seeding = TabSettings.get("enable_room_seeding", 0)

    # Clear any existing room assignments
    Round.objects.filter(round_number=TabSettings.get(
        "cur_round") - 1).update(room=None)
    round_number = TabSettings.get("cur_round") - 1

    rooms = RoomCheckIn.objects.filter(
        round_number=round_number).select_related("room").prefetch_related("room__tags")
    rooms = sorted((r.room for r in rooms), key=lambda r: r.rank, reverse=True)
    pairings = tab_logic.sorted_pairings(round_number)

    pairing_to_tag = {
        pairing: get_required_tags(pairing) for pairing in pairings
    }

    room_to_tag = {room: set(room.tags.all()) for room in rooms}

    if not room_seeding:
        random.shuffle(pairings)

    if not pairings:
        raise errors.RoomAssignmentError(
            "Attempted to assign rooms to an empty pairing")
    if len(pairings) > len(rooms):
        raise errors.RoomAssignmentError(
            f"Not enough rooms. Found {len(pairings)}\
                  rounds and only {len(rooms)} rooms")

    graph_edges = []

    for pairing_i, pairing in enumerate(pairings):
        pairing_tags = pairing_to_tag[pairing]
        for room_i, room in enumerate(rooms):
            weight = 0

            # High seed high room bonus
            if room_seeding:
                weight -= abs(pairing_i - room_i)

            # Good room bonus
            weight += room.rank * 100

            # Missing tags penalty
            missing = pairing_tags - room_to_tag[room]
            weight -= 1000 * sum(tag.priority for tag in missing)

            edge = (pairing_i, len(pairings) + room_i, weight)
            graph_edges.append(edge)

    room_assignments = mwmatching.maxWeightMatching(
        graph_edges, maxcardinality=True)

    if -1 in room_assignments[:len(pairings)]:
        pairing_list = room_assignments[: len(pairings)]
        bad_pairing = pairings[pairing_list.index(-1)]
        raise errors.RoomAssignmentError(
            f"Could not find a room for: {bad_pairing}"
        )

    updated_pairings = []
    for pairing_i, pairing in enumerate(pairings):
        room_i = room_assignments[pairing_i] - len(pairings)
        pairing.room = rooms[room_i]
        updated_pairings.append(pairing)

    with transaction.atomic():
        Round.objects.bulk_update(updated_pairings, ["room"])


def add_outround_rooms(round_specs):
    normalized_specs = []
    seen = set()
    valid_types = {Outround.VARSITY, Outround.NOVICE}
    for spec in round_specs or []:
        try:
            round_type, num_teams = int(spec[0]), int(spec[1])
        except (TypeError, ValueError, IndexError):
            continue
        if round_type not in valid_types:
            continue
        key = (round_type, num_teams)
        if key in seen:
            continue
        seen.add(key)
        normalized_specs.append(key)

    if not normalized_specs:
        raise errors.RoomAssignmentError(
            "No outround scope selected for room assignment.")

    room_seeding = TabSettings.get("enable_room_seeding", 0)
    round_priority = TabSettings.get(
        "outs_round_priority",
        OutroundRoundPriority.TOP_OF_BRACKET,
    )

    rooms = RoomCheckIn.objects.filter(
        round_number=0
    ).select_related("room").prefetch_related("room__tags")
    rooms = sorted((r.room for r in rooms), key=lambda r: r.rank, reverse=True)

    pairings = []
    for round_type, num_teams in normalized_specs:
        scoped = tab_logic.sorted_pairings(num_teams, outround=True)
        scoped = [
            p for p in scoped
            if p.type_of_round == round_type and p.victor == Outround.UNKNOWN
        ]
        pairings.extend(sort_outround_pairings(scoped, round_priority))

    if not pairings:
        return
    if len(pairings) > len(rooms):
        raise errors.RoomAssignmentError(
            f"Not enough rooms. Found {len(pairings)} "
            f"rounds and only {len(rooms)} rooms")

    pairing_to_tag = {
        pairing: get_required_tags(pairing) for pairing in pairings
    }
    room_to_tag = {room: set(room.tags.all()) for room in rooms}

    if not room_seeding:
        random.shuffle(pairings)

    graph_edges = []
    for pairing_i, pairing in enumerate(pairings):
        pairing_tags = pairing_to_tag[pairing]
        for room_i, room in enumerate(rooms):
            weight = 0

            if room_seeding:
                weight -= abs(pairing_i - room_i)

            weight += room.rank * 100

            missing = pairing_tags - room_to_tag[room]
            weight -= 1000 * sum(tag.priority for tag in missing)

            graph_edges.append((pairing_i, len(pairings) + room_i, weight))

    room_assignments = mwmatching.maxWeightMatching(
        graph_edges, maxcardinality=True)

    if -1 in room_assignments[:len(pairings)]:
        pairing_list = room_assignments[:len(pairings)]
        bad_pairing = pairings[pairing_list.index(-1)]
        raise errors.RoomAssignmentError(
            f"Could not find a room for: {bad_pairing}"
        )

    updated_pairings = []
    for pairing_i, pairing in enumerate(pairings):
        room_i = room_assignments[pairing_i] - len(pairings)
        pairing.room = rooms[room_i]
        updated_pairings.append(pairing)

    with transaction.atomic():
        Outround.objects.bulk_update(updated_pairings, ["room"])


def get_required_tags(pairing):
    """Gets required room tags from a pairing.
    Only call after using appropriate prefetches"""
    required_tags = set()

    if pairing.gov_team:
        required_tags.update(pairing.gov_team.required_room_tags.all())

    if pairing.opp_team:
        required_tags.update(pairing.opp_team.required_room_tags.all())

    if pairing.judges:
        for judge in pairing.judges.all():
            required_tags.update(judge.required_room_tags.all())

    return required_tags
