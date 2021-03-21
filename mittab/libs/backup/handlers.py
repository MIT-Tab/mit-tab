import subprocess
import os
import tempfile

from mittab import settings

DB_SETTINGS = settings.DATABASES["default"]
DB_HOST = DB_SETTINGS["HOST"]
DB_NAME = DB_SETTINGS["NAME"]
DB_USER = DB_SETTINGS["USER"]
DB_PASS = DB_SETTINGS["PASSWORD"]
DB_PORT = DB_SETTINGS["PORT"]

class MysqlDumpRestorer:

    def dump_to_file(self, fname):
        subprocess.check_call(self._dump_cmd(fname))

    def restore_from_fileobj(self, f):
        """
        This is a multi-stage restore to avoid the worst-case scenario
        where you dump the existing db, but the restore from the new db fails,
        crashing the app with an empty database

        The process is:
            1. Dump existing DB to a new file
            2. Restore from given database
            3. If error for #2, restore from the dumped file again

        Can be improved by using rename database, just need to test that out first
        """
        with tempfile.NamedTemporaryFile() as fp:
            tmp_full_path = fp.name
            subprocess.check_call(self._dump_cmd(tmp_full_path))

            try:
                with f as stdin:
                    subprocess.check_call(self._restore_cmd(), stdin=stdin)
            except Exception as e:
                subprocess.check_call(self._restore_cmd(), stdin=fp)
                raise e


    def _restore_cmd(self): # TODO: This should just be a mysql client...
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
