class MockWorkbook:
    def __init__(self, data):
        self.data = data

    def rows(self):
        return self.data
