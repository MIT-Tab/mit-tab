import os
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TransactionTestCase

from mittab.apps.tab.auth_roles import APDA_BOARD_GROUP_NAME
from mittab.apps.registration.models import InfoText, RegistrationConfig
from mittab.apps.tab.models import TabSettings
from mittab.apps.tab.tournament_start import START_TOURNAMENT_WIZARD_COMPLETED


@pytest.mark.django_db(transaction=True)
class TestInitializeTourneyCommand(TransactionTestCase):
    def test_board_user_uses_env_password_and_starts_active(self):
        with patch.dict(os.environ, {"BOARD_PASSWORD": "board-from-env"}), patch(
            "mittab.apps.tab.management.commands.initialize_tourney.backup_round"
        ):
            call_command(
                "initialize_tourney",
                tab_password="tab-password",
                entry_password="entry-password",
                first_init=True,
            )

        user_model = get_user_model()
        board_user = user_model.objects.get(username="board")
        self.assertTrue(board_user.check_password("board-from-env"))
        self.assertTrue(board_user.is_active)
        self.assertTrue(
            board_user.groups.filter(name=APDA_BOARD_GROUP_NAME).exists()
        )
        self.assertTrue(Group.objects.filter(name=APDA_BOARD_GROUP_NAME).exists())
        self.assertFalse(user_model.objects.filter(username="apda_board").exists())
        self.assertEqual(
            list(InfoText.objects.values_list("title", "text")),
            [("GA", "TBD")],
        )
        self.assertEqual(
            TabSettings.get(START_TOURNAMENT_WIZARD_COMPLETED),
            0,
        )
        self.assertFalse(
            RegistrationConfig.get_or_create_active().allow_new_registrations
        )

    def test_board_user_password_falls_back_to_random_when_env_missing(self):
        with patch.dict(
            os.environ,
            {"BOARD_PASSWORD": "", "APDA_BOARD_PASSWORD": ""},
        ), patch(
            "mittab.apps.tab.management.commands.initialize_tourney."
            "USER_MODEL.objects.make_random_password",
            return_value="random-board-password",
        ), patch("mittab.apps.tab.management.commands.initialize_tourney.backup_round"):
            call_command(
                "initialize_tourney",
                tab_password="tab-password",
                entry_password="entry-password",
                first_init=True,
            )

        board_user = get_user_model().objects.get(username="board")
        self.assertTrue(board_user.check_password("random-board-password"))
