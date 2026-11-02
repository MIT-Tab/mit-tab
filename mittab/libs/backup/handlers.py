import subprocess
import os
from functools import lru_cache

from django.conf import settings

DB_SETTINGS = settings.DATABASES["default"]
DB_HOST = DB_SETTINGS["HOST"]
DB_NAME = DB_SETTINGS["NAME"]
DB_USER = DB_SETTINGS["USER"]
DB_PASS = DB_SETTINGS["PASSWORD"]
DB_PORT = DB_SETTINGS["PORT"]
SSL_CONFIG = DB_SETTINGS.get("OPTIONS", {}).get("ssl", {})


@lru_cache(maxsize=1)
def _dump_supports_gtid_option():
    # Production's default-mysql-client can be MariaDB, whose dump command
    # does not accept MySQL's set-gtid-purged flag. Both support snapshots.
    help_text = subprocess.check_output(["mysqldump", "--help"])
    return b"set-gtid-purged" in help_text


def _ssl_cmd_args():
    args = []
    ssl_ca = SSL_CONFIG.get("ca")

    if DB_HOST not in ("127.0.0.1", "localhost"):
        args.append("--ssl")

    if ssl_ca:
        args.append(f"--ssl-ca={ssl_ca}")

    return args


class MysqlDumpRestorer:
    @staticmethod
    def _execute(command, **kwargs):
        if settings.MYSQL_SHARED_CLUSTER:
            # Failed commands are reported by subprocess/Sentry. Do not put
            # new tenant passwords in command arguments or exception text.
            kwargs["env"] = {**os.environ, "MYSQL_PWD": DB_PASS}
        return subprocess.check_output(command, **kwargs)

    def dump(self, include_scratches=True):
        return self._execute(self._dump_cmd(include_scratches=include_scratches))

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
            self._execute(self._restore_cmd(), input=content)
        except Exception as e:
            self._execute(self._restore_cmd(), input=before)
            raise e

    def _restore_cmd(self):  # Note: refactor to just use a python mysql client
        cmd = [
            "mysql",
            DB_NAME,
            f"--port={DB_PORT}",
            f"--host={DB_HOST}",
            f"--user={DB_USER}",
        ]

        if DB_PASS and not settings.MYSQL_SHARED_CLUSTER:
            cmd.append(f"--password={DB_PASS}")

        cmd.extend(_ssl_cmd_args())

        return cmd

    def _dump_cmd(self, include_scratches=True):
        cmd = [
            "mysqldump",
            DB_NAME,
            "--quick",
            "--complete-insert",
            f"--port={DB_PORT}",
            f"--host={DB_HOST}",
            f"--user={DB_USER}",
        ]

        if settings.MYSQL_SHARED_CLUSTER:
            # InnoDB snapshots need no server-wide lock, PROCESS or global GTID
            # changes. A tenant's backup/restore must stay inside its schema.
            cmd.extend(
                [
                    "--single-transaction",
                    "--skip-lock-tables",
                    "--skip-add-locks",
                    "--no-tablespaces",
                ]
            )
            if _dump_supports_gtid_option():
                cmd.append("--set-gtid-purged=OFF")
        else:
            cmd.append("--lock-all-tables")

        if DB_PASS and not settings.MYSQL_SHARED_CLUSTER:
            cmd.append(f"--password={DB_PASS}")

        cmd.extend(_ssl_cmd_args())

        if not include_scratches:
            cmd.append(f"--ignore-table={DB_NAME}.tab_scratch")

        return cmd
