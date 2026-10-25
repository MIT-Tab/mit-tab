from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase

from mittab.apps.tab.models import School


class TestEnsureTournamentInitializedCommand(TransactionTestCase):
    def setUp(self):
        self.addCleanup(self._drop_initialization_flag)
        self._drop_initialization_flag()

    @staticmethod
    def _drop_initialization_flag():
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS tournament_initialized")

    @staticmethod
    def _is_initialized():
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE 'tournament_initialized'")
            return cursor.fetchone() is not None

    def test_test_tournament_redeploy_preserves_edited_fixture_data(self):
        with patch(
            "mittab.apps.tab.management.commands.initialize_tourney.backup_round"
        ):
            call_command(
                "ensure_tournament_initialized",
                tab_password="tab-password",
                load_test_fixture=True,
            )

        self.assertTrue(self._is_initialized())
        school = School.objects.get(name="NYU")
        school.name = "Edited NYU"
        school.save()
        school_count = School.objects.count()

        with patch(
            "mittab.apps.tab.management.commands."
            "ensure_tournament_initialized.call_command"
        ) as setup_command:
            call_command(
                "ensure_tournament_initialized",
                tab_password="tab-password",
                load_test_fixture=True,
            )

        setup_command.assert_not_called()
        school.refresh_from_db()
        self.assertEqual(school.name, "Edited NYU")
        self.assertEqual(School.objects.count(), school_count)

    def test_regular_tournament_does_not_load_fixture(self):
        with patch(
            "mittab.apps.tab.management.commands.initialize_tourney.backup_round"
        ):
            call_command("ensure_tournament_initialized", tab_password="tab-password")

        self.assertTrue(self._is_initialized())
        self.assertFalse(School.objects.exists())

    def test_fixture_failure_does_not_mark_tournament_initialized(self):
        with patch(
            "mittab.apps.tab.management.commands."
            "ensure_tournament_initialized.call_command",
            side_effect=[None, RuntimeError("fixture failed")],
        ) as setup_command:
            with self.assertRaisesMessage(RuntimeError, "fixture failed"):
                call_command(
                    "ensure_tournament_initialized",
                    tab_password="tab-password",
                    load_test_fixture=True,
                )

        self.assertEqual(
            setup_command.call_args_list[-1].args, ("loaddata", "testing_db")
        )
        self.assertFalse(self._is_initialized())
