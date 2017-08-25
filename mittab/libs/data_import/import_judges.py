from mittab.libs.data_import import JudgeImporter

def import_judges(fileToImport):
    try:
        importer = JudgeImporter(fileToImport)
    except:
        return ["ERROR: Please upload an .xlsx file. This filetype is not compatible"]

    importer.save()
    return importer.errors
