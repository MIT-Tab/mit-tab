#!/usr/bin/env python2
from django.core.management import setup_environ

try:
    import settings  # Assumed to be in the same directory.
except ImportError:
    import sys
    sys.stderr.write(
        "Error: Can't find the file 'settings.py' in the directory containing %r. It appears you've customized things.\nYou'll have to run django-admin.py, passing it your settings module.\n(If the file settings.py does indeed exist, it's causing an ImportError somehow.)\n"
        % __file__)
    sys.exit(1)


def sep():
    print("#" * 80)


if __name__ == "__main__":
    setup_environ(settings)
    from mittab.libs.data_import import import_judges, import_teams, import_scratches
    print("Importing Judges")
    sep()
    try:
        import_judges.import_judges("../JudgeEntry.xls")
    except:
        print("There was an error importing judges")
    sep()
    try:
        import_teams.import_teams("../TeamEntry.xls")
    except:
        print("There was an error importing teams")
    sep()
    try:
        import_scratches.import_scratches("../ScratchEntry.xls")
    except:
        print("There was an error importing scratches")
    sep()
