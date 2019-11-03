import shutil
import time
import os
from wsgiref.util import FileWrapper

from django.conf import settings

from mittab.apps.tab.models import TabSettings
from mittab.libs import errors
from mittab.settings import BASE_DIR
from mittab.libs.backup.strategies.local_dump import LocalDump


def _generate_unique_key(s):
    if LocalDump(s).exists():
        return "%s_%s" % (s, int(time.time()))
    else:
        return s

def backup_round(dst_filename=None, round_number=None, btime=None):
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
    print("Restoring from backups directory")
    return LocalDump(src_key).restore()


def get_wrapped_file(src_key):
    return LocalDump(src_key).downloadable()
