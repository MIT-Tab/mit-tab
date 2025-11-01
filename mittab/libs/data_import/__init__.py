from abc import ABC, abstractmethod

from django.forms import ModelForm
import xlrd
from xlrd.biffh import XLRDError


class InvalidWorkbookException(Exception):
    pass


class Workbook:
    """
    A wrapper for an xlsx file, so that we can mock it in tests
    """

    def __init__(self, file_to_import, min_rows):
        self.min_rows = min_rows
        try:
            self.sheet = xlrd.open_workbook(
                filename=None,
                file_contents=file_to_import.read()).sheet_by_index(0)
        except (XLRDError, TypeError, IndexError) as exc:
            raise InvalidWorkbookException("Could not open workbook") from exc

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
            while self.get(row, col) is not None or col < self.min_rows:
                row_data.append(self.get(row, col) or "")
                col += 1
            yield row_data
            row += 1


class WorkbookImporter(ABC):
    min_row_size = 1

    def __init__(self, workbook):
        self.workbook = workbook
        self.errors = []
        self.created_objs = []

    @abstractmethod
    def import_row(self, row, row_number):
        pass

    def import_data(self):
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
            self.errors.append(f"Row {row_number + 1}: {msg}")
        else:
            self.errors.append(msg)
