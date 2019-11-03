import shutil
import time
import os
from wsgiref.util import FileWrapper

from django.conf import settings

from mittab.apps.tab.models import TabSettings
from mittab.libs import errors
from mittab.settings import BASE_DIR
from mittab.libs.backup.strategies.local_dump import LocalDump


def backup_round(dst_filename=None, round_number=None, btime=None):
    if round_number is None:
        round_number = TabSettings.get("cur_round", "no-round-number")

    if btime is None:
        btime = int(time.time())

    print("Trying to backup to backups directory")
    if dst_filename is None:
        dst_filename = "site_round_%i_%i" % (round_number, btime)

    if LocalDump(dst_filename).exists():
        dst_filename += "_%i" % btime

    return LocalDump(dst_filename).backup()


def handle_backup(f):
    dst_filename = get_backup_filename(f.name)
    print(("Tried to write {}".format(dst_filename)))
    try:
        with open(dst_filename, "wb+") as destination:
            for chunk in f.chunks():
                destination.write(chunk)
    except Exception:
        errors.emit_current_exception()


def list_backups():
    print("Checking backups directory")
    return [ dump.key for dump in LocalDump.all() ]


def restore_from_backup(src_key):
    print("Restoring from backups directory")
    return LocalDump(src_key).restore()


def get_wrapped_file(src_key):
    return LocalDump(src_key).downloadable()
