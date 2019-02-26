from contextlib import contextmanager
import shutil
import time
import os

from mittab.apps.tab.models import TabSettings
from django.conf import settings
from wsgiref.util import FileWrapper
from mittab.settings import BASE_DIR
from mittab.libs import errors


BACKUP_PREFIX = os.path.join(BASE_DIR, "mittab")
BACKUP_PATH = os.path.join(BACKUP_PREFIX, "backups")
DATABASE_PATH = settings.DATABASES['default']['NAME']


def get_backup_filename(filename):
    if len(filename) < 3 or filename[-3:] != ".db":
        filename += ".db"
    return os.path.join(BACKUP_PATH, filename)

def backup_exists(filename):
    return os.path.exists(get_backup_filename(filename))

def backup_round(dst_filename = None, round_number = None, btime = None):
    if round_number is None:
        round_number = TabSettings.get("cur_round")

    if btime is None:
        btime = int(time.time())

    print("Trying to backup to backups directory")
    if dst_filename == None:
        dst_filename = "site_round_%i_%i" % (round_number, btime)

    if backup_exists(dst_filename):
        dst_filename += "_%i" % btime

    return copy_db(DATABASE_PATH, get_backup_filename(dst_filename))

def handle_backup(f):
    dst_filename = get_backup_filename(f.name)
    print(("Tried to write {}".format(dst_filename)))
    try:
        with open(dst_filename, "wb+") as destination:
            for chunk in f.chunks():
                destination.write(chunk)
    except Exception as e:
        errors.emit_current_exception()

def list_backups():
    print("Checking backups directory")
    if not os.path.exists(BACKUP_PATH):
        os.makedirs(BACKUP_PATH)

    return os.listdir(BACKUP_PATH)

def restore_from_backup(src_filename):
    print("Restoring from backups directory")
    return copy_db(get_backup_filename(src_filename), DATABASE_PATH)

def copy_db(src_filename, dst_filename):
    try:
        shutil.copyfile(src_filename, dst_filename)
        print(("Copied %s to %s" % (src_filename, dst_filename)))
        return True
    except:
        errors.emit_current_exception()
        return False

def get_wrapped_file(src_filename):
    src_filename = get_backup_filename(src_filename)
    return FileWrapper(open(src_filename, "rb")), os.path.getsize(src_filename)
