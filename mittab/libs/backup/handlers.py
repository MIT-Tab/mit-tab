import subprocess

from mittab import settings

DB_SETTINGS = settings.DATABASES["default"]
DB_HOST = DB_SETTINGS["HOST"]
DB_NAME = DB_SETTINGS["NAME"]
DB_USER = DB_SETTINGS["USER"]
DB_PASS = DB_SETTINGS["PASSWORD"]
DB_PORT = DB_SETTINGS["PORT"]


class MysqlDumpRestorer:

    def dump(self, include_scratches=True):
        return subprocess.check_output(self._dump_cmd(
            include_scratches=include_scratches))

    def restore(self, content):
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
        before = self.dump()
        try:
            subprocess.check_output(self._restore_cmd(), input=content)
        except Exception as e:
            subprocess.check_output(self._restore_cmd(), input=before)
            raise e

    def _restore_cmd(self):  # Note: refactor to just use a python mysql client
        cmd = [
            "mysql",
            DB_NAME,
            "--port={}".format(DB_PORT),
            "--host={}".format(DB_HOST),
            "--user={}".format(DB_USER),
        ]

        if DB_PASS:
            cmd.append("--password={}".format(DB_PASS))

        # DigitalOcean Managed Databases require SSL but use self-signed certificates.
        # --ssl-mode=REQUIRED enables encrypted connection, --ssl-ca=/dev/null skips
        # certificate verification to avoid "self-signed certificate" errors while
        # still maintaining encrypted communication to the database.
        if DB_HOST and DB_HOST != "127.0.0.1" and DB_HOST != "localhost":
            cmd.append("--ssl-mode=REQUIRED")
            cmd.append("--ssl-ca=/dev/null")

        return cmd

    def _dump_cmd(self, include_scratches=True):
        cmd = [
            "mysqldump",
            DB_NAME,
            "--quick",
            "--lock-all-tables",
            "--complete-insert",
            "--port={}".format(DB_PORT),
            "--host={}".format(DB_HOST),
            "--user={}".format(DB_USER),
        ]

        if DB_PASS:
            cmd.append("--password={}".format(DB_PASS))

        # DigitalOcean Managed Databases require SSL but use self-signed certificates.
        # --ssl-mode=REQUIRED enables encrypted connection, --ssl-ca=/dev/null skips
        # certificate verification to avoid "self-signed certificate" errors while
        # still maintaining encrypted communication to the database.
        if DB_HOST and DB_HOST != "127.0.0.1" and DB_HOST != "localhost":
            cmd.append("--ssl-mode=REQUIRED")
            cmd.append("--ssl-ca=/dev/null")

        if not include_scratches:
            cmd.append("--ignore-table={}.tab_scratch".format(DB_NAME))

        return cmd
