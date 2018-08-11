#!/usr/bin/env python2
import logging

from django.core.management import setup_environ

__log = logging.getLogger(__name__)

try:
    import settings # Assumed to be in the same directory.
except ImportError:
    import sys
    __log.exception("Error: Can't find the file 'settings.py' in the directory containing %r. It appears you've customized things.\nYou'll have to run django-admin.py, passing it your settings module.\n(If the file settings.py does indeed exist, it's causing an ImportError somehow.)\n" % __file__)
    sys.exit(1)

if __name__ == "__main__":
    setup_environ(settings)
    import import_judges
    import import_teams
    import import_scratches
    __log.debug("Importing data")
    try:
        import_judges.import_judges("../JudgeEntry.xls")
    except:
        __log.exception("There was an error importing judges")
    try:
        import_teams.import_teams("../TeamEntry.xls")
    except:
        __log.exception("There was an error importing teams")
    try:
        import_scratches.import_scratches("../ScratchEntry.xls")
    except:
        __log.exception("There was an error importing scratches")
