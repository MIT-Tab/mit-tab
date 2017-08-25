from mittab.libs.data_import.base import TeamImporter

def import_teams(fileToImport):
    try:
        importer = TeamImporter(fileToImport)
    except:
        return ['ERROR: Please upload an .xlsx file. This filetype is not compatible']

    importer.save()
    return importer.errors

