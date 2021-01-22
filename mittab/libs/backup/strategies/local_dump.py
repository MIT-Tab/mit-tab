import os
import time
import subprocess
from wsgiref.util import FileWrapper

from mittab import settings


BACKUP_PREFIX = os.path.join(settings.BASE_DIR, "mittab")
BACKUP_PATH = os.path.join(BACKUP_PREFIX, "backups")
SUFFIX = ".dump.sql"

if not os.path.exists(BACKUP_PATH):
    os.makedirs(BACKUP_PATH)

DB_SETTINGS = settings.DATABASES["default"]
DB_HOST = DB_SETTINGS["HOST"]
DB_NAME = DB_SETTINGS["NAME"]
DB_USER = DB_SETTINGS["USER"]
DB_PASS = DB_SETTINGS["PASSWORD"]
DB_PORT = DB_SETTINGS["PORT"]


class LocalDump:
    def __init__(self, key):
        self.key = key

    @classmethod
    def all(cls):
        all_names = os.listdir(BACKUP_PATH)
        def key_from_filename(name):
            return name[:-len(SUFFIX)]
        return [cls(key_from_filename(name)) for name in all_names]

    @classmethod
    def from_upload(cls, key, upload):
        dst_filename = os.path.join(BACKUP_PATH, key + SUFFIX)
        with open(dst_filename, "wb+") as destination:
            for chunk in upload.chunks():
                destination.write(chunk)
        return cls(key)

    def downloadable(self):
        src_filename = self._get_backup_filename()
        return FileWrapper(open(src_filename, "rb")), os.path.getsize(src_filename)

    def backup(self):
        subprocess.check_call(self._dump_cmd(self._get_backup_filename()))

    def restore(self):
        tmp_filename = "backup_before_restore_%s%s" % (int(time.time()), SUFFIX)
        tmp_full_path = os.path.join(BACKUP_PATH, tmp_filename)
        try:
            subprocess.check_call(self._dump_cmd(tmp_full_path))
        except Exception as e:
            os.remove(tmp_full_path)
            raise e

        try:
            with open(self._get_backup_filename()) as stdin:
                subprocess.check_call(self._restore_cmd(), stdin=stdin)
            os.remove(tmp_full_path)
        except Exception as e:
            with open(tmp_full_path) as stdin:
                subprocess.check_call(self._restore_cmd(), stdin=stdin)
            raise e

    def exists(self):
        return os.path.exists(self._get_backup_filename())

    def _get_backup_filename(self):
        key = self.key
        if len(key) < len(SUFFIX) or not key.endswith(SUFFIX):
            key += SUFFIX
        return os.path.join(BACKUP_PATH, key)

    def _restore_cmd(self):
        cmd = [
            "mysql",
            DB_NAME,
            "--port={}".format(DB_PORT),
            "--host={}".format(DB_HOST),
            "--user={}".format(DB_USER),
        ]

        if DB_PASS:
            cmd.append("--password={}".format(DB_PASS))

        return cmd


    def _dump_cmd(self, dst):
        return ['ls']
        cmd = [
            "mysqldump",
            DB_NAME,
            "--quick",
            "--lock-all-tables",
            "--port={}".format(DB_PORT),
            "--host={}".format(DB_HOST),
            "--user={}".format(DB_USER),
            "--result-file={}".format(dst),
        ]

        if DB_PASS:
            cmd.append("--password={}".format(DB_PASS))

        return cmd
