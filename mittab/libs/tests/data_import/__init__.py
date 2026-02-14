class MockWorkbook:
    def __init__(self, data, header=None):
        self.data = data
        self._header = header

    def rows(self):
        return self.data

    def header(self):
        return self._header
