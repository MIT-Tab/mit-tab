from abc import ABC, abstractmethod

from django.forms import ModelForm
import xlrd


class InvalidWorkbookException(Exception):
    pass


class Workbook:
    """
    A wrapper for an xlsx file, so that we can mock it in tests
    """

    def __init__(self, file_to_import):
        try:
            self.sheet = xlrd.open_workbook(
                filename=None, file_contents=self.file_to_import.read()
            ).sheet_by_index(0)
        except:
            raise InvalidWorkbookException("Could not open workbook")

    def get(self, row, col):
        try:
            return str(self.sheet.cell(row, col).value)
        except IndexError:
            return None

    def rows(self):
        """
        A simple iterator to return all of the rows as a list of strings
        """
        row = 1
        while self.get(row, 0) is not None:
            col = 0
            row_data = []
            while self.get(row, col) is not None:
                row_data.append(self.get(row, col))
                col += 1
            yield row_data
            row += 1


class WorkbookImporter(ABC):
    min_row_size = 1
    name = "Base Importer"

    def __init__(self, workbook):
        self.workbook = workbook
        self.errors = []
        self.created_objs = []

    @abstractmethod
    def import_row(self, row):
        pass

    def import_data(self):
        is_incorrect_size = lambda row: len(row) < self.min_row_size is None
        if any(map(is_incorrect_size, self.workbook.rows())):
            self.error("Insuficient columns in sheet. No data read.")
        else:
            for row_number, row in enumerate(self.workbook.rows()):
                self.import_row(row, row_number)
            if self.errors:
                self.rollback()
        return self.errors

    def create(self, obj):
        if isinstance(obj, ModelForm):
            self.created_objs.append(obj.instance)
        else:
            self.created_objs.append(obj)
        obj.save()
        return obj

    def rollback(self):
        for obj in reversed(self.created_objs):
            obj.delete()

    def error(self, msg, row_number=None):
        if row_number is not None:
            self.errors.append("%s row %d: %s" % (self.name, row_number + 1, msg))
        else:
            self.errors.append("%s: %s" % (self.name, msg))
