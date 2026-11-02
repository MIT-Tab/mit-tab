"""Opt-in real restricted-user boot, registration, backup and restore test.

Uses disposable schemas/accounts only; requires a private MySQL test server
whose configured test administrator can CREATE USER and CREATE DATABASE.
"""

import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import MySQLdb
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from mittab.libs.backup.handlers import MysqlDumpRestorer


@unittest.skipUnless(
    os.environ.get("MITTAB_TEST_SHARED_MYSQL") == "1",
    "requires an explicitly enabled private MySQL test server",
)
class SharedMysqlIntegrationTest(SimpleTestCase):
    def test_restricted_tournaments_boot_backup_restore_and_keep_tenants_separate(self):
        database = settings.DATABASES["default"]
        params = {
            "host": database["HOST"],
            "port": int(database["PORT"] or 3306),
            "user": database["USER"],
            "passwd": database["PASSWORD"],
        }
        admin = MySQLdb.connect(**params)
        admin.autocommit(True)
        cursor = admin.cursor()
        names = ["verify" + uuid.uuid4().hex[:20] for _ in range(2)]
        password = uuid.uuid4().hex
        try:
            for name in names:
                cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
                cursor.execute(
                    "CREATE USER %s@%s IDENTIFIED BY %s", (name, "%", password)
                )
                cursor.execute(
                    "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, "
                    "ALTER, INDEX, "
                    "REFERENCES, CREATE TEMPORARY TABLES, SHOW VIEW, TRIGGER, "
                    "LOCK TABLES "
                    f"ON `{name}`.* "
                    "TO %s@%s",
                    (name, "%"),
                )
                environment = os.environ.copy()
                environment.update(
                    {
                        "MYSQL_HOST": params["host"],
                        "MYSQL_PORT": str(params["port"]),
                        "MYSQL_DATABASE": name,
                        "MYSQL_USER": name,
                        "MYSQL_PASSWORD": password,
                        "MYSQL_SHARED_CLUSTER": "true",
                        "BACKUP_STORAGE": "",
                        "SENTRY_DSN": "",
                        "MITTAB_FAKE_WEBPACK_LOADER": "1",
                        "AWS_EC2_METADATA_DISABLED": "true",
                    }
                )
                root = Path(settings.BASE_DIR)
                for arguments in [
                    [sys.executable, "manage.py", "migrate", "--noinput"],
                    [
                        sys.executable,
                        "manage.py",
                        "ensure_tournament_initialized",
                        "--tab-password",
                        "SyntheticOnly12",
                    ],
                ]:
                    result = subprocess.run(
                        arguments,
                        cwd=root,
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=120,
                    )
                    self.assertEqual(
                        result.returncode, 0, result.stderr.decode()[-2000:]
                    )
                # Exercise the real Django registration flow under that tenant's
                # credentials. Only outgoing email/directory calls are mocked.
                code = """
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mittab.settings")
import django
django.setup()
from django.test import Client
from unittest.mock import patch
from mittab.apps.registration.models import Registration, RegistrationConfig
from mittab.apps.registration.tests.test_registration_flow import (
    speaker, team_entry, registration_payload,
)
config=RegistrationConfig.get_or_create_active()
config.allow_new_registrations=True
config.save()
label=os.environ["MYSQL_DATABASE"]
team=team_entry(0,"Synthetic Team",[
    speaker("Speaker One","custom:"+label,label),
    speaker("Speaker Two","custom:"+label,label),
])
payload=registration_payload("custom:"+label,label,"contact@example.com",[team],[])
with (
    patch("mittab.apps.registration.services.fetch_remote_schools",return_value=[]),
    patch("mittab.apps.registration.emails.EmailService") as service,
):
    service.return_value.send_bulk.return_value=1
    response=Client().post("/registration/",data=payload)
assert response.status_code==302,response.status_code
assert Registration.objects.count()==1
"""
                result = subprocess.run(
                    [sys.executable, "-c", code],
                    cwd=root,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode()[-2000:])

            first, second = names
            tenant = MySQLdb.connect(
                **{**params, "user": first, "passwd": password, "db": first}
            )
            # Test reads must not hold a metadata lock while the restore CLI
            # replaces tables through its separate connection.
            tenant.autocommit(True)
            query = tenant.cursor()
            try:
                for sql in [
                    f"SELECT * FROM `{second}`.tab_team",
                    f"DROP DATABASE `{second}`",
                    "FLUSH TABLES WITH READ LOCK",
                ]:
                    with self.assertRaises(MySQLdb.Error):  # pylint: disable=no-member
                        query.execute(sql)
                with (
                    override_settings(MYSQL_SHARED_CLUSTER=True),
                    patch.multiple(
                        "mittab.libs.backup.handlers",
                        DB_NAME=first,
                        DB_USER=first,
                        DB_PASS=password,
                        DB_HOST=params["host"],
                        DB_PORT=str(params["port"]),
                        SSL_CONFIG={},
                    ),
                ):
                    handler = MysqlDumpRestorer()
                    snapshot = handler.dump()
                    self.assertIn(first.encode(), snapshot)
                    self.assertNotIn(second.encode(), snapshot)
                    query.execute("UPDATE tab_team SET name='Changed after backup'")
                    tenant.commit()
                    handler.restore(snapshot)
                    # Older standalone dumps emit schema-local LOCK statements.
                    # They remain importable without any global privileges.
                    legacy_snapshot = (
                        snapshot + b"\nLOCK TABLES tab_team WRITE; UNLOCK TABLES;\n"
                    )
                    handler.restore(legacy_snapshot)
                    query.execute("SELECT name FROM tab_team")
                    self.assertEqual(query.fetchone()[0], "Synthetic Team")
                    with self.assertRaises(subprocess.CalledProcessError):
                        handler.restore(b"THIS IS NOT VALID SQL;")
                    tenant.rollback()
                    query.execute("SELECT name FROM tab_team")
                    self.assertEqual(query.fetchone()[0], "Synthetic Team")
                cursor.execute(f"SELECT name FROM `{second}`.tab_team")
                self.assertEqual(cursor.fetchone()[0], "Synthetic Team")
            finally:
                query.close()
                tenant.close()
        finally:
            for name in names:
                cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
                cursor.execute("DROP USER IF EXISTS %s@%s", (name, "%"))
            cursor.close()
            admin.close()
