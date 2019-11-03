import os
from io import StringIO
from shutil import copyfileobj
from wsgiref.util import FileWrapper

from django.core.management import call_command

from mittab.settings import BASE_DIR


BACKUP_PREFIX = os.path.join(BASE_DIR, "mittab")
BACKUP_PATH = os.path.join(BACKUP_PREFIX, "backups")
SUFFIX = ".dump.json"

if not os.path.exists(BACKUP_PATH):
    os.makedirs(BACKUP_PATH)

class LocalDump:
    def __init__(self, key):
        self.key = key

    @classmethod
    def all(cls):
        all_names = os.listdir(BACKUP_PATH)
        def key_from_filename(name):
            return name[:-len(SUFFIX)]
        return [ cls(key_from_filename(name)) for name in all_names ]

    def downloadable(self):
        src_filename = self._get_backup_filename()
        return FileWrapper(open(src_filename, "rb")), os.path.getsize(src_filename)

    def backup(self):
        out = StringIO()
        call_command("dumpdata", stdout=out)
        with open(self._get_backup_filename(), 'w') as f:
            out.seek(0)
            copyfileobj(out, f)

    def restore(self):
        return call_command("loaddata", self._get_backup_filename())

    def exists(self):
        return os.path.exists(self._get_backup_filename())

    def _get_backup_filename(self):
        key = self.key
        if len(key) < len(SUFFIX) or not key.endswith(SUFFIX):
            key += ".dump.json"
        return os.path.join(BACKUP_PATH, key)
