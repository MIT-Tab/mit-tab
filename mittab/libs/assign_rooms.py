from mittab.apps.tab.models import Room, TabSettings
from errors import RoomAssignmentError


def add_rooms(pairings):
    round_number = TabSettings.get('cur_round') - 1
    rounds_and_priorities = [
        (pairing, room_group_preferences(pairing)) for pairing in pairings
    ]

    for round_obj, priority_list in rounds_and_priorities:
        round_obj.room = None
        if len(priority_list) == 0:
            continue

        for room_group in priority_list:
            best_room = get_best_room_for_group(room_group, round_number)
            if best_room:
                round_obj.room = best_room
                round_obj.save()
                break

    for round_obj in pairings:
        if round_obj.room is not None:
            continue

        best_room = get_best_room_for_group(None, round_number)
        if best_room is None:
            raise RoomAssignmentError()
        round_obj.room = best_room
        round_obj.save()


def get_best_room_for_group(room_group, round_number):
    query = Room.available_for_round(round_number).order_by('-rank')
    if room_group:
        query = query.filter(groups=room_group)
    return query.first()


def room_group_preferences(round_obj):
    people_in_round = list(round_obj.judges.all()) + [round_obj.gov_team, round_obj.opp_team]
    room_groups = map(lambda person: person.room_group_priority, people_in_round)
    return filter(lambda group: not (group is None or group.unavailable), room_groups)
