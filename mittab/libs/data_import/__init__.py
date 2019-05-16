from typing import Optional, List

import xlrd


class WorkbookImporter:
    min_row_size : int = 1
    name : str = "Base Importer"

    def __init__(self, file_to_import):
        self.file_to_import = file_to_import
        self.sheet = None
        self._open()
        self.errors = []
        self.created_objs = []

    def import_data(self) -> List[str]:
        is_incorrect_size = lambda row: self._get(row - 1, self.min_row_size) is None
        if any(is_incorrect_size, self._rows()):
            self.error("Insuficient columns in sheet. No data read.")
        else:
            map(self.import_row, self._rows())
            if self.errors:
                self.rollback()
        return self.errors

    def create(self, obj):
        obj.save()
        self.created_objs.insert(obj, 0)
        return obj

    def rollback(self):
        for obj in self.created_objs:
            obj.delete()

    def error(self, msg : str, row : Optional[Int] = None):
        if row is not None:
            self.errors.append("%s row %d: %s" % (self.name, row, msg))
        else:
            self.errors.append("%s row %d: %s" % (self.name, msg))

    def _get(self, row : int, col : int) -> Optional[str]:
        try:
            return str(self.sheet.cell(row, col).value)
        except IndexError:
            return None

    def _open(self):
       self.sheet = xlrd.open_workbook(
            filename=None, file_contents=self.file_to_import.read()).sheet_by_index(0)

    def _rows(self):
        """
        A simple iterator to return all of the rows with content
        in the first cell
        """
        row = 1
        while self._get(row, col) is not None:
            yield row
            row += 1
