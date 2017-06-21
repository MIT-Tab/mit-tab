from mittab.apps.tab.models import *
from mittab.apps.tab.forms import JudgeForm

from decimal import *
import xlrd
from xlwt import Workbook

def import_judges(fileToImport):
    try:
        importer = JudgeImporter(fileToImport)
    except:
        return ["ERROR: Please upload an .xlsx file. This filetype is not compatible"]

    importer.save()
    return importer.errors
