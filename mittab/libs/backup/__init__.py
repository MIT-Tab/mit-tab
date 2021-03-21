import os
import tempfile
import time
from wsgiref.util import FileWrapper

from django.conf import settings

from mittab.apps.tab.models import TabSettings
from mittab.libs import errors
from mittab.libs.backup.handlers import MysqlDumpRestorer
from mittab.libs.backup.storage import LocalFilesystem, ObjectStorage
from mittab import settings

ACTIVE_BACKUP_KEY = "MITTAB_ACTIVE_BACKUP"
ACTIVE_BACKUP_VAL = "1"

if settings.BACKUPS["use_s3"]:
    BACKUP_STORAGE = ObjectStorage()
else:
    BACKUP_STORAGE = LocalFilesystem()
BACKUP_HANDLER = MysqlDumpRestorer()


# TODO: Improve this to be.... something better and more lock-y
class ActiveBackupContextManager:
    def __enter__(self):
        os.environ[ACTIVE_BACKUP_KEY] = ACTIVE_BACKUP_VAL
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        os.environ[ACTIVE_BACKUP_KEY] = "0"

def _generate_unique_key(base):
    if BACKUP_STORAGE.exists(base):
        return "%s_%s" % (base, int(time.time()))
    else:
        return base

def backup_round(key=None, round_number=None, btime=None):
    with ActiveBackupContextManager() as _:
        if round_number is None:
            round_number = TabSettings.get("cur_round", "no-round-number")

        if btime is None:
            btime = int(time.time())

        print("Trying to backup to backups directory")
        if key is None:
            key = "site_round_%i_%i" % (round_number, btime)
        key = _generate_unique_key(key)

        with tempfile.NamedTemporaryFile() as fp:
            BACKUP_HANDLER.dump_to_file(fp.name)
            BACKUP_STORAGE.store_fileobj(key, fp)

def upload_backup(f):
    key = _generate_unique_key(f.name)
    print(("Tried to write {}".format(key)))
    try:
        BACKUP_STORAGE.store_fileobj(key, f)
    except Exception:
        errors.emit_current_exception()

def get_backup_fileobj(key):
    return BACKUP_STORAGE.get_fileobj(key)

def list_backups():
    print("Checking backups directory")
    return BACKUP_STORAGE.all()

def restore_from_backup(key):
    with ActiveBackupContextManager() as _:
        print("Restoring from backups directory")
        backup_fileobj = BACKUP_STORAGE.get_fileobj(key)
        BACKUP_HANDLER.restore_from_fileobj(backup_fileobj)

def is_backup_active():
    return str(os.environ.get(ACTIVE_BACKUP_KEY, "0")) == ACTIVE_BACKUP_VAL
