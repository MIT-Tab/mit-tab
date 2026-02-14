from django.test import TestCase
import pytest

from mittab.apps.tab.models import Team, Judge, Scratch
from mittab.libs.tests.data_import import MockWorkbook
from mittab.libs.data_import.import_scratches import ScratchImporter


@pytest.mark.django_db
class TestImportingScratches(TestCase):
    pytestmark = pytest.mark.django_db(transaction=True)
    fixtures = ["testing_db"]

    def test_valid_scratches(self):
        orig_team_count = Team.objects.count()
        orig_judge_count = Judge.objects.count()
        orig_scratch_count = Scratch.objects.count()

        assert not Scratch.objects.filter(
            team__name="AU Elle Woods",
            judge__name="Adrienne Martinez",
            scratch_type=Scratch.TEAM_SCRATCH).exists()
        assert not Scratch.objects.filter(
            team__name="Bates Blouse",
            judge__name="Christine Mercado",
            scratch_type=Scratch.TAB_SCRATCH).exists()

        data = [["AU Elle Woods", "Adrienne Martinez", "team scratch"],
                ["Bates Blouse", "Christine Mercado", "tab"]]
        importer = ScratchImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert not errors
        assert Scratch.objects.count() == orig_scratch_count + 2
        assert Judge.objects.count() == orig_judge_count
        assert Team.objects.count() == orig_team_count

        assert Scratch.objects.filter(
            team__name="AU Elle Woods",
            judge__name="Adrienne Martinez",
            scratch_type=Scratch.TEAM_SCRATCH).exists()
        assert Scratch.objects.filter(
            team__name="Bates Blouse",
            judge__name="Christine Mercado",
            scratch_type=Scratch.TAB_SCRATCH).exists()

    def test_invalid_scratches(self):
        orig_team_count = Team.objects.count()
        orig_judge_count = Judge.objects.count()
        orig_scratch_count = Scratch.objects.count()

        data = [["AU Elle Woods", "Judge doesn't exist", "team scratch"],
                ["Bates Blouse", "Christine Mercado", "invalid type"],
                ["Team doesn't exist", "Christine Mercado", "tab"],
                ["AU Elle Woods", "Adrienne Martinez", "team scratch"]]
        importer = ScratchImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert len(errors) == 3
        assert Scratch.objects.count() == orig_scratch_count
        assert Judge.objects.count() == orig_judge_count
        assert Team.objects.count() == orig_team_count
        assert "Row 1: Judge 'Judge doesn't exist' does not exist" in errors
        assert "Row 2: 'invalid type' is not a valid scratch type" in errors
        assert "Row 3: Team 'Team doesn't exist' does not exist" in errors

    def test_header_mismatch_gives_clear_column_order_error(self):
        orig_team_count = Team.objects.count()
        orig_judge_count = Judge.objects.count()
        orig_scratch_count = Scratch.objects.count()

        header = ["Judge", "Team", "Scratch Type"]
        data = [["AU Elle Woods", "Adrienne Martinez", "team scratch"]]
        importer = ScratchImporter(MockWorkbook(data, header=header))
        errors = importer.import_data()

        assert len(errors) == 2
        assert errors[0] == \
            "Header mismatch in the scratches file at column 1: expected 'Team', got 'Judge'."
        assert errors[1].startswith(
            "Please keep the header row and column order from the template. Expected order:"
        )
        assert Scratch.objects.count() == orig_scratch_count
        assert Judge.objects.count() == orig_judge_count
        assert Team.objects.count() == orig_team_count
