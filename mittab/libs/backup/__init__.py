import shutil
import time
import os
from wsgiref.util import FileWrapper

from django.conf import settings

from mittab.apps.tab.models import TabSettings
from mittab.libs import errors
from mittab.settings import BASE_DIR
from mittab.libs.backup.strategies.local_dump import LocalDump


ACTIVE_BACKUP_KEY = "MITTAB_ACTIVE_BACKUP"
ACTIVE_BACKUP_VAL = "1"

# TODO: Improve this to be.... something better and more lock-y
class ActiveBackupContextManager:
    def __enter__(self):
        os.environ[ACTIVE_BACKUP_KEY] = ACTIVE_BACKUP_VAL
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        os.environ[ACTIVE_BACKUP_KEY] = "0"

def _generate_unique_key(base):
    if LocalDump(base).exists():
        return "%s_%s" % (base, int(time.time()))
    else:
        return base

def backup_round(dst_filename=None, round_number=None, btime=None):
    with ActiveBackupContextManager() as _:
        if round_number is None:
            round_number = TabSettings.get("cur_round", "no-round-number")

        if btime is None:
            btime = int(time.time())

        print("Trying to backup to backups directory")
        if dst_filename is None:
            dst_filename = "site_round_%i_%i" % (round_number, btime)

        dst_filename = _generate_unique_key(dst_filename)
        return LocalDump(dst_filename).backup()


def handle_backup(f):
    dst_key = _generate_unique_key(f.name)
    print(("Tried to write {}".format(dst_key)))
    try:
        return LocalDump.from_upload(dst_key, f)
    except Exception:
        errors.emit_current_exception()


def list_backups():
    print("Checking backups directory")
    return [dump.key for dump in LocalDump.all()]


def restore_from_backup(src_key):
    with ActiveBackupContextManager() as _:
        print("Restoring from backups directory")
        return LocalDump(src_key).restore()


def get_wrapped_file(src_key):
    return LocalDump(src_key).downloadable()

def is_backup_active():
    return str(os.environ.get(ACTIVE_BACKUP_KEY, "0")) == ACTIVE_BACKUP_VAL
