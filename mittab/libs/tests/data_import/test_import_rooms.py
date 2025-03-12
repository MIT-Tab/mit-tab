from django.test import TestCase
import pytest

from mittab.apps.tab.models import Room
from mittab.libs.tests.data_import import MockWorkbook
from mittab.libs.data_import.import_rooms import RoomImporter


@pytest.mark.django_db(transaction=True)
class TestImportingJudges(TestCase):
    fixtures = ["testing_empty"]

    def test_valid_rooms(self):
        assert Room.objects.count() == 0

        data = [["Room 1", "10.22"], ["Room 2", "20"], ["Room 3", "30.5"]]
        importer = RoomImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert not errors
        assert Room.objects.count() == 3

        room_1 = Room.objects.get(name="Room 1")
        assert float(room_1.rank) == 10.22
        room_2 = Room.objects.get(name="Room 2")
        assert float(room_2.rank) == 20.0
        room_3 = Room.objects.get(name="Room 3")
        assert float(room_3.rank) == 30.5

    def test_rollback_when_invalid_room(self):
        assert Room.objects.count() == 0

        data = [["Room 1", "10.22"], ["Room 2", "200000"], ["Room 1", "30.5"]]
        importer = RoomImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert Room.objects.count() == 0
        assert len(errors) == 2
        assert "Row 3: Room 1 - Room with this Name already exists." in errors
        assert "Row 2: Room 2 - Ensure that there are no more than" \
            " 4 digits in total." in errors
