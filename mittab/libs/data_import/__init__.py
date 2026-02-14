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

    def header(self):
        col = 0
        row_data = []
        while self.get(0, col) is not None or col < self.min_rows:
            row_data.append((self.get(0, col) or "").strip())
            col += 1
        return row_data if row_data else None


class WorkbookImporter(ABC):
    min_row_size = 1
    expected_headers = []
    file_label = "data"

    def __init__(self, workbook):
        self.workbook = workbook
        self.errors = []
        self.created_objs = []

    @abstractmethod
    def import_row(self, row, row_number):
        pass

    def import_data(self):
        self.validate_headers()
        if self.errors:
            return self.errors

        for row_number, row in enumerate(self.workbook.rows()):
            try:
                self.import_row(row, row_number)
            except Exception as exc:
                self.error(
                    "Could not parse this row. Please verify that your file "
                    "uses the correct template and column order "
                    f"({exc.__class__.__name__}).",
                    row_number)
                break
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

    @staticmethod
    def _normalize_header_value(value):
        return " ".join(str(value).strip().lower().split())

    def validate_headers(self):
        if not self.expected_headers:
            return

        header_getter = getattr(self.workbook, "header", None)
        if not callable(header_getter):
            return

        header = header_getter()
        if header is None:
            return

        normalized_header = [self._normalize_header_value(h) for h in header]

        for idx, (expected_label, aliases) in enumerate(self.expected_headers):
            actual = normalized_header[idx] if idx < len(normalized_header) else ""
            normalized_aliases = [self._normalize_header_value(a) for a in aliases]
            if actual not in normalized_aliases:
                actual_display = header[idx].strip() if idx < len(header) and header[idx] else "(blank)"
                expected_order = ", ".join(
                    label for label, _aliases in self.expected_headers
                )
                self.error(
                    f"Header mismatch in the {self.file_label} file at column "
                    f"{idx + 1}: expected '{expected_label}', got "
                    f"'{actual_display}'.")
                self.error(
                    "Please keep the header row and column order from the "
                    f"template. Expected order: {expected_order}"
                )
                return
