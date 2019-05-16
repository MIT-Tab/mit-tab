import xlrd


class Workbook:
    def __init__(self, file_to_import):
        self.file_to_import = file_to_import
        self.sheet = None
        self._open()


    def _get(self, row, col):
        try:
            return self.sheet.cell(row, col).value
        except IndexError:
            return None

    def _open(self):
        sh = xlrd.open_workbook(
            filename=None, file_contents=self.file_to_import.read()).sheet_by_index(0)
