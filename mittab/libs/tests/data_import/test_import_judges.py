from django.test import TestCase
import pytest

from mittab.apps.tab.models import School, Judge, JudgeExpectedCheckIn, TabSettings
from mittab.libs.tests.data_import import MockWorkbook
from mittab.libs.data_import.import_judges import JudgeImporter


@pytest.mark.django_db
class TestImportingJudges(TestCase):
    pytestmark = pytest.mark.django_db(transaction=True)
    fixtures = ["testing_empty"]

    def test_valid_judges(self):
        assert Judge.objects.count() == 0
        assert School.objects.count() == 0

        TabSettings.set("tot_rounds", 2)

        data = [["Judge 1", "9.5", "Harvard", "yes", ""],
                ["Judge 2", "10.5555", "Yale", "Harvard", "Northeastern", "1", "0"],
                ["Judge 3", "20", "", "", ""]]
        importer = JudgeImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert not errors
        assert Judge.objects.count() == 3
        assert School.objects.count() == 3

        judge_1 = Judge.objects.get(name="Judge 1")
        assert float(judge_1.rank) == 9.5
        assert judge_1.name == "Judge 1"
        assert sorted(map(lambda s: s.name,
                          judge_1.schools.all())) == ["Harvard"]

        judge_2 = Judge.objects.get(name="Judge 2")
        assert float(judge_2.rank) == 10.56
        assert judge_2.name == "Judge 2"
        assert sorted(map(lambda s: s.name, judge_2.schools.all())) == \
            ["Harvard", "Northeastern", "Yale"]

        judge_3 = Judge.objects.get(name="Judge 3")
        assert float(judge_3.rank) == 20.0
        assert judge_3.name == "Judge 3"
        assert not judge_3.schools.all()
        assert JudgeExpectedCheckIn.objects.filter(judge=judge_1,
                                                   round_number=1).exists()
        assert not JudgeExpectedCheckIn.objects.filter(judge=judge_1,
                                                       round_number=2).exists()

    def test_rollback_from_duplicate(self):
        assert Judge.objects.count() == 0
        assert School.objects.count() == 0

        data = [["Judge 1", "9.5", "Harvard"],
                ["Judge 2", "10.5555", "Yale", "Harvard", "Northeastern"],
                ["Judge 1", "20"]]
        importer = JudgeImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert Judge.objects.count() == 0
        assert School.objects.count() == 0
        assert len(errors) == 1
        assert errors[
            0] == "Row 3: Judge 1 - Judge with this Name already exists."

    def test_rollback_from_invalid_rank(self):
        assert Judge.objects.count() == 0
        assert School.objects.count() == 0

        data = [["Judge 1", "9.5", "Harvard"],
                ["Judge 2", "200", "Yale", "Harvard", "Northeastern"],
                ["Judge 3", "20"]]
        importer = JudgeImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert Judge.objects.count() == 0
        assert School.objects.count() == 0
        assert len(errors) == 1
        assert errors[0] == "Row 2: Judge 2 - Ensure that there are no" \
            " more than 2 digits before the decimal point."

    def test_schools_not_rolledback_if_existed_before(self):
        school = School(name="NU")
        school.save()

        assert Judge.objects.count() == 0
        assert School.objects.count() == 1

        data = [["Judge 1", "10000", "NU"]]
        importer = JudgeImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert Judge.objects.count() == 0
        assert School.objects.count() == 1
        assert len(errors) == 1
        assert errors[0] == "Row 1: Judge 1 - Ensure that there are" \
            " no more than 4 digits in total."

    def test_expected_rounds_imported(self):
        TabSettings.set("tot_rounds", 3)

        data = [["Judge 4", "9.5", "Harvard", "Y", "", "1"]]
        importer = JudgeImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert not errors
        judge = Judge.objects.get(name="Judge 4")
        expected_rounds = sorted(
            JudgeExpectedCheckIn.objects.filter(judge=judge).values_list(
                "round_number", flat=True)
        )
        assert expected_rounds == [1, 3]

    def test_blank_school_cells_ignored(self):
        data = [["Judge 5", "9.5", "", "  ", ""]]
        importer = JudgeImporter(MockWorkbook(data))
        errors = importer.import_data()

        assert not errors
        judge = Judge.objects.get(name="Judge 5")
        assert judge.schools.count() == 0
        assert School.objects.count() == 0
