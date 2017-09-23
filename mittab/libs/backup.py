import shutil
import time
import os

from mittab.apps.tab.models import TabSettings
from django.conf import settings
from django.core.servers.basehttp import FileWrapper
from mittab.settings import BASE_DIR


BACKUP_PREFIX = os.path.join(BASE_DIR, "mittab")
BACKUP_PATH = os.path.join(BACKUP_PREFIX, "backups")


def get_backup_filename(filename):
    if len(filename) < 3 or filename[-4:-1] is not ".db":
        filename += ".db"
    return os.path.join(BACKUP_PATH, filename)

def backup_exists(filename):
    return os.path.exists(get_backup_filename(filename))

def backup_round(dst_filename = None, round_number = None, btime = None):
    if round_number is None:
        round_number = TabSettings.get("cur_round")

    if btime is None:
        btime = int(time.time())

    print("Attempting to backup to backups directory")
    if dst_filename == None:
        dst_filename = "site_round_%i_%i" % (round_number, btime)

    if backup_exists(dst_filename):
        dst_filname += "_%i" % btime

    dst_filename = get_backup_filename(dst_filename)
    src_filename = settings.DATABASES['default']['NAME']

    try:
        shutil.copy(src_filename, dst_filename)
        print("Copied %s to %s" % (src_filename, dst_filename))
    except:
        print("Could not copy %s to %s; most likely non-existant file"%(src_filename, dst_filename))

def handle_backup(f):
    dst_filename = get_backup_filename(f.name)
    print("Tried to write {}".format(dst_filename))
    try:
        with open(dst_filename, "wb+") as destination:
            for chunk in f.chunks():
                destination.write(chunk)
    except Exception as e:
        print("Could not write {}".format(dst_filename))
        print("ERROR: {}".format(str(e)))

def list_backups():
    print("Checking backups directory")

    if not os.path.exists(BACKUP_PATH):
        os.makedirs(BACKUP_PATH)

    return os.listdir(BACKUP_PATH)

def restore_from_backup(src_filename):
    print("Restoring from backups directory")
    src_filename = get_backup_filename(src_filename)
    dst_filename = settings.DATABASES['default']['NAME']

    try:
        shutil.copy(src_filename, dst_filename)
        print("Copied %s to %s" % (src_filename, dst_filename))
    except:
        print("Could not copy %s to %s; most likely non-existant file" % (src_filename, dst_filename))

def get_wrapped_file(src_filename):
    src_filename = get_backup_filename(src_filename)
    return FileWrapper(open(src_filename, "rb")), os.path.getsize(src_filename)
