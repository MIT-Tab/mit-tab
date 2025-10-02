import io
import os
import tempfile
import time
from datetime import datetime
from wsgiref.util import FileWrapper
import pytz


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
BEFORE_ROOM_ASSIGN = 5
BEFORE_BREAK = 6
UPLOAD = 7
OTHER = 8
TYPE_CHOICES = (
    (MANUAL, "Manual"),
    (INITAL, "Inital"),
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

def _name_backup(btype=None, round_number=None, btime=None, include_scratches=True, name=None):
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

    scratches_flag = "1" if include_scratches else "0"
    return f"{name}_{btype}_{round_number}_{btime}_{scratches_flag}"

def backup_round(btype=None, round_number=None, btime=None, name=None, include_scratches=True):
    filename = _name_backup(btype, round_number, btime, include_scratches, name)
    with ActiveBackupContextManager() as _:
        print("Trying to backup to backups directory")
        BACKUP_STORAGE[filename] = BACKUP_HANDLER.dump(include_scratches=include_scratches)

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
    """
    Dynamically process backup filename fields to extract metadata.
    Returns a list with 6 elements: [filename, name, type, round_num, timestamp, scratches]
    Gracefully handles corrupted or incomplete filenames without raising exceptions.
    """
    # Default values if parsing fails
    default_metadata = [
        filename,      # 0: key/filename
        "Unknown",     # 1: name
        "Unknown",     # 2: type
        "Unknown",     # 3: round_num
        "Unknown",     # 4: timestamp
        "Unknown"      # 5: scratches
    ]
    
    try:
        data = filename.split("_")
        
        # We need at least 4 parts for a valid backup (name, btype, round, time)
        if len(data) < 4:
            return default_metadata
        
        # Process each field individually with error handling
        metadata = [filename]  # Start with the filename
        
        # Field 1: Name (always use if available)
        try:
            metadata.append(data[0])
        except (IndexError, Exception):
            metadata.append("Unknown")
        
        # Field 2: Backup type
        try:
            btype = int(data[1])
            if 0 <= btype < len(TYPE_CHOICES):
                metadata.append(TYPE_CHOICES[btype][1])
            else:
                metadata.append("Unknown Type")
        except (ValueError, IndexError, Exception):
            metadata.append("Unknown")
        
        # Field 3: Round number
        try:
            metadata.append(str(data[2]))
        except (IndexError, Exception):
            metadata.append("Unknown")
        
        # Field 4: Timestamp
        try:
            btime = int(data[3])
            est = pytz.timezone('US/Eastern')
            backup_time = datetime.fromtimestamp(btime, tz=pytz.UTC).astimezone(est)
            current_time = datetime.now(est)
            
            backup_date = backup_time.date()
            current_date = current_time.date()
            
            if backup_date == current_date:
                formatted_time = f"Today at {backup_time.strftime('%I:%M %p')}"
            elif (current_date - backup_date).days == 1:
                formatted_time = f"Yesterday at {backup_time.strftime('%I:%M %p')}"
            else:
                formatted_time = backup_time.strftime("%b %d at %I:%M %p")
            
            metadata.append(formatted_time)
        except (ValueError, IndexError, OSError, Exception):
            metadata.append("Unknown")
        
        # Field 5: Scratches flag (optional, only in newer backups)
        try:
            if len(data) >= 5:
                scratches_flag = data[4]
                if scratches_flag == "1":
                    metadata.append("Yes")
                elif scratches_flag == "0":
                    metadata.append("No")
                else:
                    metadata.append("Unknown")
            else:
                # Legacy format without scratches flag
                metadata.append("Unknown")
        except (IndexError, Exception):
            metadata.append("Unknown")
        
        return metadata
        
    except Exception:
        # Catch-all for any unexpected errors - return defaults
        return default_metadata

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
