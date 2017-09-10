def value_or_empty(sheet, row, column):
    """
    Read the value at the row & column
    return '' if the value is empty or if the row/column pair doesnt exist
    """
    try:
        val = sheet.cell(row, column).value
    except IndexError:
        val = ''
    finally:
        return val or ''
