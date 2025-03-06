import io
import os
import tempfile
import time
from wsgiref.util import FileWrapper


from mittab.apps.tab.models import TabSettings
from mittab.libs import errors, cache_logic
from mittab.libs.backup.handlers import MysqlDumpRestorer
from mittab.libs.backup.storage import LocalFilesystem, ObjectStorage
from mittab import settings

ACTIVE_BACKUP_KEY = "MITTAB_ACTIVE_BACKUP"
ACTIVE_BACKUP_VAL = "1"

MANUAL = 0
INITAL = 1
BEFORE_NEW_TOURNAMENT = 2
BEFORE_JUDGE_ASSIGN = 3
BEFORE_PAIRING = 4
BEFORE_BREAK = 5
UPLOAD = 6
OTHER = 7
TYPE_CHOICES = (
    (MANUAL, "Manual"),
    (INITAL, "Inital"),
    (BEFORE_NEW_TOURNAMENT, "Before New Tournament"),
    (BEFORE_JUDGE_ASSIGN, "Before Judge Assign"),
    (BEFORE_PAIRING, "Before Pairing"),
    (BEFORE_BREAK, "Before the Break"),
    (UPLOAD, "Upload"),
    (OTHER, "Other"),
)

if settings.BACKUPS["use_s3"]:
    BACKUP_STORAGE = ObjectStorage()
else:
    BACKUP_STORAGE = LocalFilesystem()
BACKUP_HANDLER = MysqlDumpRestorer()


# Note: Improve this to be.... something better and more lock-y
class ActiveBackupContextManager:
    def __enter__(self):
        os.environ[ACTIVE_BACKUP_KEY] = ACTIVE_BACKUP_VAL
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        os.environ[ACTIVE_BACKUP_KEY] = "0"
        cache_logic.clear_cache()

def _name_backup(btype=None, round_number=None, btime=None, name=None):
    if round_number is None:
        round_number = TabSettings.get("cur_round", "no-round-number")

    if btime is None:
        btime = int(time.time())

    if btype is None:
        btype = OTHER

    if name is None:
        name = f"{TYPE_CHOICES[btype][1]} Round {round_number}"

    return f"{name}_{btype}_{round_number}_{btime}"

def backup_round(btype=None, round_number=None, btime=None, name=None):
    filename = _name_backup(btype, round_number, btime, name)
    with ActiveBackupContextManager() as _:
        print("Trying to backup to backups directory")
        BACKUP_STORAGE[filename] = BACKUP_HANDLER.dump()

def upload_backup(f):
    filename = _name_backup(
        btype=UPLOAD,
        name=f.name,
        round_number="Unknown",
        )
    print(f"Writing {f.name} to backups directory")
    try:
        BACKUP_STORAGE[filename] = f.read()
    except Exception:
        errors.emit_current_exception()


def get_backup_content(key):
    return BACKUP_STORAGE[key]

def get_metadata(filename):
    print(filename)
    data = filename.split("_")
    if len(data) == 4:
        name, btype, round_number, btime = data
        return [
            filename,
            name,
            TYPE_CHOICES[int(btype)][1],
            round_number,
            time.strftime("%B %d at %I:%M %p", time.localtime(int(btime)))
        ]
    else:
        return [
            filename,
            filename,
            "Unknown",
            "Unknown",
            "Unknown"
        ]

def list_backups():
    print("Checking backups directory")
    keys = BACKUP_STORAGE.keys()
    metadata = []
    for key in keys:
        metadata.append(get_metadata(key))
    return metadata


def restore_from_backup(key):
    with ActiveBackupContextManager() as _:
        print("Restoring from backups directory")
        BACKUP_HANDLER.restore(BACKUP_STORAGE[key])


def is_backup_active():
    return str(os.environ.get(ACTIVE_BACKUP_KEY, "0")) == ACTIVE_BACKUP_VAL
