"""Compare native room assignment with the existing outround room workflow."""

from contextlib import nullcontext
from decimal import Decimal
from random import Random
from types import SimpleNamespace
from unittest.mock import patch

from mittab.apps.tab.models import Outround
from mittab.libs import assign_rooms


class RoomTag:
    def __init__(self, tag_id, priority_hundredths):
        self.id = tag_id
        self.priority_hundredths = priority_hundredths
        self.priority = Decimal(priority_hundredths) / 100


class Row(SimpleNamespace):
    __hash__ = object.__hash__
    __eq__ = object.__eq__


def room_cases():
    rng = Random(91723)
    for index in range(200):
        tags = [RoomTag(tag_id, rng.randint(0, 600)) for tag_id in range(rng.randint(0, 5))]
        size = rng.randint(1, 14)
        debates = [
            Row(
                type_of_round=Outround.VARSITY,
                victor=Outround.UNKNOWN,
                required=set(rng.sample(tags, rng.randint(0, len(tags)))),
            )
            for _ in range(size)
        ]
        rooms = []
        for room_id in range(size + rng.randint(0, 5)):
            rank = rng.choice((-100, 0, 0, 20, 75, 900))
            present = set(rng.sample(tags, rng.randint(0, len(tags))))
            rooms.append(Row(
                id=room_id, rank=Decimal(rank) / 100, rank_hundredths=rank,
                tags=SimpleNamespace(all=lambda present=present: present),
            ))
        seeded = index % 2 == 1
        # The solver receives room-ID order even when check-ins arrive in reverse.
        checkins = [Row(room=room) for room in reversed(rooms)]
        with (
            patch.object(assign_rooms.TabSettings, "get",
                         side_effect=lambda key, default=0: seeded if key == "enable_room_seeding" else default),
            patch.object(assign_rooms.RoomCheckIn.objects, "filter") as checks,
            patch.object(assign_rooms.tab_logic, "sorted_pairings", return_value=debates.copy()),
            patch.object(assign_rooms, "get_required_tags", side_effect=lambda pairing: pairing.required),
            patch.object(assign_rooms, "sort_outround_pairings", side_effect=lambda pairings, _: pairings),
            patch.object(assign_rooms.random, "shuffle"),  # The caller supplies pairing order.
            patch.object(assign_rooms.transaction, "atomic", side_effect=nullcontext),
            patch.object(Outround.objects, "bulk_update"),
        ):
            checks.return_value.select_related.return_value.prefetch_related.return_value = checkins
            assign_rooms.add_outround_rooms([(Outround.VARSITY, size * 2)])
        fields = ["rooms", int(seeded), len(debates)]
        for debate in debates:
            fields.append(len(debate.required))
            for tag in sorted(debate.required, key=lambda tag: tag.id):
                fields.extend((tag.id, tag.priority_hundredths))
        fields.append(len(rooms))
        for room in rooms:
            present = room.tags.all()
            fields.extend((room.rank_hundredths, len(present), *(tag.id for tag in present)))
        yield " ".join(map(str, fields)), ",".join(str(debate.room.id) for debate in debates)
