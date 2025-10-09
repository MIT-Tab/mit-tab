import io
import os
import tempfile
import time
from datetime import datetime, timezone, timedelta
from wsgiref.util import FileWrapper


from mittab.apps.tab.models import TabSettings
from mittab.libs import errors, cache_logic
from mittab.libs.backup.handlers import MysqlDumpRestorer
from mittab.libs.backup.storage import LocalFilesystem, ObjectStorage
from mittab import settings

ACTIVE_BACKUP_KEY = "MITTAB_ACTIVE_BACKUP"
ACTIVE_BACKUP_VAL = "1"

MANUAL = 0
INITIAL = 1
BEFORE_NEW_TOURNAMENT = 2
BEFORE_JUDGE_ASSIGN = 3
BEFORE_PAIRING = 4
BEFORE_ROOM_ASSIGN = 5
BEFORE_BREAK = 6
UPLOAD = 7
OTHER = 8
TYPE_CHOICES = (
    (MANUAL, "Manual"),
    (INITIAL, "Initial"),
    (BEFORE_NEW_TOURNAMENT, "Before New Tournament"),
    (BEFORE_JUDGE_ASSIGN, "Before Judge Assign"),
    (BEFORE_PAIRING, "Before Pairing"),
    (BEFORE_ROOM_ASSIGN, "Before Room Assign"),
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

def _name_backup(btype=None, round_number=None, btime=None,
                 include_scratches=True, name=None):
    if round_number is None:
        round_number = TabSettings.get("cur_round", "no-round-number")
    else:
        # Convert round_number to string to support both int and string inputs
        round_number = str(round_number)

    if btime is None:
        btime = int(time.time())

    if btype is None:
        btype = OTHER

    if name is None:
        name = f"{TYPE_CHOICES[btype][1]} Round {round_number}"

    if include_scratches in ("Unknown", None):
        scratches_flag = "U"
    else:
        scratches_flag = "1" if include_scratches else "0"
    return f"{name}_{btype}_{round_number}_{btime}_{scratches_flag}"

def backup_round(btype=None, round_number=None, btime=None,
                 name=None, include_scratches=True):
    filename = _name_backup(btype, round_number, btime,
                            include_scratches, name)
    with ActiveBackupContextManager() as _:
        print("Trying to backup to backups directory")
        BACKUP_STORAGE[filename] = BACKUP_HANDLER.dump(include_scratches=
                                                       include_scratches)

def upload_backup(f):
    filename = _name_backup(
        btype=UPLOAD,
        name=f.name,
        round_number="Unknown",
        include_scratches="Unknown",
        )
    print(f"Writing {f.name} to backups directory")
    try:
        BACKUP_STORAGE[filename] = f.read()
    except Exception:
        errors.emit_current_exception()


def get_backup_content(key):
    return BACKUP_STORAGE[key]

def get_metadata(filename):
    data = filename.split("_")
    defaults = [filename, "Unknown", "Unknown", "Unknown", "Unknown", "Unknown"]

    if len(data) < 4:
        return defaults

    name = data[0] if data else filename

    try:
        btype = int(data[1])
        type_exists = 0 <= btype < len(TYPE_CHOICES)
        backup_type = TYPE_CHOICES[btype][1] if type_exists else "Unknown"
    except (ValueError, IndexError):
        backup_type = "Unknown"

    round_num = str(data[2]) if len(data) > 2 else "Unknown"

    try:
        est = timezone(timedelta(hours=-5))
        backup_time = datetime.fromtimestamp(int(data[3]),
                                             tz=timezone.utc).astimezone(est)
        days_ago = (datetime.now(est).date() - backup_time.date()).days

        if days_ago == 0:
            timestamp = f"Today at {backup_time.strftime('%I:%M %p')}"
        elif days_ago == 1:
            timestamp = f"Yesterday at {backup_time.strftime('%I:%M %p')}"
        else:
            timestamp = backup_time.strftime("%b %d at %I:%M %p")
    except (ValueError, IndexError, OSError):
        timestamp = "Unknown"

    scratches_code = data[4] if len(data) >= 5 else None
    if scratches_code == "1":
        scratches = "Yes"
    elif scratches_code == "0":
        scratches = "No"
    else:
        scratches = "Unknown"

    return [filename, name, backup_type, round_num, timestamp, scratches]

def list_backups():
    print("Checking backups directory")
    return [get_metadata(key) for key in BACKUP_STORAGE.keys()]


def restore_from_backup(key):
    with ActiveBackupContextManager() as _:
        print("Restoring from backups directory")
        BACKUP_HANDLER.restore(BACKUP_STORAGE[key])


def is_backup_active():
    return str(os.environ.get(ACTIVE_BACKUP_KEY, "0")) == ACTIVE_BACKUP_VAL
