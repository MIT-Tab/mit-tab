
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
        round_obj.room = best_room
        round_obj.save()


def get_best_room_for_group(room_group, round_number):
    base_query = Room.objects.filter(group__checked_in=True) \
            .exclude(round__round_number=round_number) \
            .order_by('-group__rank')

    if room_group is None:
        return base_query.first()
    else:
        return base_query.filter(group=room_group).first()


def room_group_preferences(round_obj):
    people_in_round = list(round_obj.judges.all()) + [round_obj.gov_team, round_obj.opp_team]
    room_groups = map(lambda person: person.room_group_priority, people_in_round)
    room_groups = filter(lambda group: group is not None and group.checked_in, room_groups)
    return sorted(room_groups, key=lambda group: group.rank, reverse=True)
