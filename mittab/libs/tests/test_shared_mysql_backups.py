"""Shared-cluster dumps must work without global administration privileges."""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from mittab.libs.backup.handlers import MysqlDumpRestorer


class SharedMysqlBackupTest(SimpleTestCase):
    def setUp(self):
        super().setUp()
        detector = patch(
            "mittab.libs.backup.handlers._dump_supports_gtid_option", return_value=True
        )
        detector.start()
        self.addCleanup(detector.stop)

    @override_settings(MYSQL_SHARED_CLUSTER=False)
    def test_legacy_dumps_retain_server_lock(self):
        command = MysqlDumpRestorer()._dump_cmd()  # pylint: disable=protected-access
        self.assertIn("--lock-all-tables", command)
        self.assertNotIn("--single-transaction", command)

    @override_settings(MYSQL_SHARED_CLUSTER=True)
    def test_shared_dump_uses_schema_scoped_snapshot(self):
        command = MysqlDumpRestorer()._dump_cmd()  # pylint: disable=protected-access
        for flag in (
            "--single-transaction",
            "--skip-lock-tables",
            "--skip-add-locks",
            "--no-tablespaces",
            "--set-gtid-purged=OFF",
        ):
            self.assertIn(flag, command)
        self.assertNotIn("--lock-all-tables", command)
        self.assertNotIn("--all-databases", command)
        self.assertNotIn("--databases", command)

    @override_settings(MYSQL_SHARED_CLUSTER=True)
    @patch("mittab.libs.backup.handlers.DB_PASS", "synthetic-sensitive-value")
    @patch(
        "mittab.libs.backup.handlers.subprocess.check_output", return_value=b"snapshot"
    )
    def test_shared_password_is_absent_from_command_and_exception_arguments(
        self, execute
    ):
        MysqlDumpRestorer().dump()
        command = execute.call_args.args[0]
        self.assertFalse(any("synthetic-sensitive-value" in part for part in command))
        self.assertEqual(
            execute.call_args.kwargs["env"]["MYSQL_PWD"], "synthetic-sensitive-value"
        )

    @override_settings(MYSQL_SHARED_CLUSTER=True)
    @patch("mittab.libs.backup.handlers.DB_NAME", "tenant_a")
    def test_scratches_remain_excludable_with_shared_snapshot(self):
        command = MysqlDumpRestorer()._dump_cmd(  # pylint: disable=protected-access
            include_scratches=False,
        )
        self.assertIn("--ignore-table=tenant_a.tab_scratch", command)

    @override_settings(MYSQL_SHARED_CLUSTER=True)
    def test_mariadb_client_does_not_receive_mysql_only_option(self):
        with patch(
            "mittab.libs.backup.handlers._dump_supports_gtid_option", return_value=False
        ):
            command = MysqlDumpRestorer()._dump_cmd()  # pylint: disable=protected-access
        self.assertIn("--single-transaction", command)
        self.assertNotIn("--set-gtid-purged=OFF", command)
        self.assertNotIn("--lock-all-tables", command)

    @override_settings(MYSQL_SHARED_CLUSTER=True)
    @patch("mittab.libs.backup.handlers.subprocess.check_output")
    def test_failed_restore_restores_original_tenant_snapshot(self, execute):
        execute.side_effect = [b"current tenant snapshot", RuntimeError("bad SQL"), b""]
        with self.assertRaisesRegex(RuntimeError, "bad SQL"):
            MysqlDumpRestorer().restore(b"invalid upload")
        self.assertEqual(
            execute.call_args_list[-1].kwargs["input"], b"current tenant snapshot"
        )
