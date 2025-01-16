import random
from mittab.apps.tab.models import RoomCheckIn, Round, TabSettings
from mittab.libs import errors, mwmatching, tab_logic
from django.db import transaction


def add_rooms():
    no_seeding = TabSettings.get("disable_room_seeding", 0)

    # Clear any existing room assignments
    Round.objects.filter(round_number=TabSettings.get(
        "cur_round") - 1).update(room=None)
    round_number = TabSettings.get("cur_round") - 1

    rooms = RoomCheckIn.objects.filter(
        round_number=round_number).select_related("room")
    rooms = sorted((r.room for r in rooms), key=lambda r: r.rank, reverse=True)
    pairings = tab_logic.sorted_pairings(round_number)

    if no_seeding:
        random.shuffle(pairings)

    if not pairings or not rooms or len(pairings) > len(rooms):
        raise errors.RoomAssignmentError("Not enough rooms or pairings")

    graph_edges = []

    for pairing_i, pairing in enumerate(pairings):
        for room_i, room in enumerate(rooms):
            # I chose to put this logic directly into the loop because some later changes that aren't
            # in this branch require some data prep that made breaking the weight calculation out
            # a lot less natural, and this ended up being a bit more readable
            weight = 0

            # High seed high room bonus
            if no_seeding == 0:
                weight -= abs(pairing_i - room_i)

            # Bad room penalty
            weight -= room.rank

            edge = (pairing_i, len(pairings) + room_i, weight)
            graph_edges.append(edge)

    # This is of course super overkill for the required logic in this branch
    # but even with just ~3 room tags it was pretty easy to find scenarios
    # where the simpler/more naive greedy algorithms would fail. The overhead
    # isn't too bad, (it was under 0.02 seconds on my system)
    # and since we already use this elsewhere it seemed reasonable
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
        Round.objects.bulk_update(updated_pairings, ['room'])
