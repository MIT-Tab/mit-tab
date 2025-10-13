import random
from django.db import transaction
from mittab.apps.tab.models import RoomCheckIn, Round, TabSettings
from mittab.libs import errors, mwmatching, tab_logic


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
            "Could not find a room for: %s" % str(bad_pairing)
        )

    updated_pairings = []
    for pairing_i, pairing in enumerate(pairings):
        room_i = room_assignments[pairing_i] - len(pairings)
        pairing.room = rooms[room_i]
        updated_pairings.append(pairing)

    with transaction.atomic():
        Round.objects.bulk_update(updated_pairings, ["room"])


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
