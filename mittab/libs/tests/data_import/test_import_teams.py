from django.test import TestCase
import pytest

from mittab.apps.tab.models import School, Debater, Team
from mittab.libs.tests.data_import import MockWorkbook
from mittab.libs.data_import.import_teams import TeamImporter


@pytest.mark.django_db
class TestImportingTeams(TestCase):
    pytestmark = pytest.mark.django_db
    fixtures = ["testing_empty"]

    def test_valid_teams(self):
        assert Team.objects.count() == 0
        assert School.objects.count() == 0
        assert Debater.objects.count() == 0

        data = [
            ["Team 1", "NU", "Deis", "full", "John", "", "1241", "Jane", "n", "1242"],
            ["Team 2", "Harvard", "", "", "Alice", "", "1251", "Bob", "", "1252"],
            ["Team 3", "Deis", "Harvard", "Half Seed ", "Carly", "1111", "Novice", "Dan", "Novice ", "1112"]
        ]

        importer = TeamImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert not errors
        assert School.objects.count() == 3
        assert Team.objects.count() == 3
        assert Debater.objects.count() == 6

        team_1 = Team.objects.get(name="Team 1")
        assert team_1.school.name == "NU"
        assert team_1.hybrid_school.name == "Deis"
        assert team_1.seed == Team.FULL_SEED

        debater_1 = team_1.debaters.get(name="John")
        debater_2 = team_1.debaters.get(name="Jane")
        assert debater_1.novice_status == Debater.VARSITY
        assert debater_2.novice_status == Debater.NOVICE

        team_2 = Team.objects.get(name="Team 2")
        assert team_2.school.name == "Harvard"
        assert team_2.hybrid_school is None
        assert team_2.seed == Team.UNSEEDED

        debater_1 = team_2.debaters.get(name="Alice")
        debater_2 = team_2.debaters.get(name="Bob")
        assert debater_1.novice_status == Debater.VARSITY
        assert debater_2.novice_status == Debater.VARSITY

        team_3 = Team.objects.get(name="Team 3")
        assert team_3.school.name == "Deis"
        assert team_3.hybrid_school.name == "Harvard"
        assert team_3.seed == Team.HALF_SEED

        debater_1 = team_3.debaters.get(name="Carly")
        debater_2 = team_3.debaters.get(name="Dan")
        assert debater_1.novice_status == Debater.NOVICE
        assert debater_2.novice_status == Debater.NOVICE

    def test_rollback_from_duplicate_debater(self):
        assert Team.objects.count() == 0
        assert School.objects.count() == 0
        assert Debater.objects.count() == 0

        data = [["Team 1", "NU", "Deis", "full", "John", "", "Jane", "n"],
                ["Team 2", "Harvard", "", "", "Alice", "", "John", ""]]

        importer = TeamImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert Team.objects.count() == 0
        assert School.objects.count() == 0
        assert Debater.objects.count() == 0
        assert len(errors) == 1
        assert errors[
            0] == "Row 2: John - Debater with this Name already exists."

    def test_rollback_from_invalid_team(self):
        assert Team.objects.count() == 0
        assert School.objects.count() == 0
        assert Debater.objects.count() == 0

        data = [["Team 1", "NU", "Deis", "full", "John", "", "Jane", "n"],
                ["Team 2", "Harvard", "", "invalid", "Alice", "", "Bob", ""]]

        importer = TeamImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert Team.objects.count() == 0
        assert School.objects.count() == 0
        assert Debater.objects.count() == 0
        assert len(errors) == 1
        assert errors[0] == "Row 2: Invalid seed value for team Team 2"

    def test_schools_not_rolledback_if_existed_before(self):
        school = School(name="NU")
        school.save()

        assert Debater.objects.count() == 0
        assert Team.objects.count() == 0
        assert School.objects.count() == 1

        data = [["Team 1", "NU", "Deis", "invalid", "John", "", "Jane", "n"]]
        importer = TeamImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert Debater.objects.count() == 0
        assert Team.objects.count() == 0
        assert School.objects.count() == 1
        assert School.objects.first().name == "NU"
        assert len(errors) == 1
        assert errors[0] == "Row 1: Invalid seed value for team Team 1"
