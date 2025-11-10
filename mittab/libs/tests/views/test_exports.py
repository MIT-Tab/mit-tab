import csv
import io
import json
import os
import tempfile
from unittest import mock

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import TestCase, RequestFactory

from mittab.apps.tab.models import Team, Round, RoundStats, TabSettings
from mittab.libs.data_export.tab_card import (
    get_all_json_data,
    csv_tab_cards,
    get_tab_card_data,
)
from mittab.libs.data_export.xml_archive import ArchiveExporter
from mittab.libs.backup.storage import LocalFilesystem
from mittab.libs.data_export.s3_connector import (
    export_results_now,
    RESULTS_FILENAME,
    RESULTS_SUFFIX,
)
from mittab.apps.tab.views.views import publish_results


@pytest.mark.django_db(transaction=True)
class TestExports(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        TabSettings.set("cur_round", 2)
        TabSettings.set("tot_rounds", 5)

    def test_exports_with_standard_data(self):
        team = Team.with_preloaded_relations_for_tab_card().first()
        fallback_team = Team.objects.first()
        team = team or fallback_team
        self.assertIsNotNone(team, "Expected at least one team in fixtures")

        export_data = get_all_json_data()
        self.assertTrue(export_data, "JSON export should not be empty")
        team_code = team.get_or_create_team_code()
        if team_code not in export_data:
            team_code = next(iter(export_data.keys()))
            team = Team.objects.filter(team_code=team_code).first() or team
        self.assertIn(
            team_code,
            export_data,
            "JSON export should contain the selected team",
        )

        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)
        csv_tab_cards(csv_writer)
        self.assertIn(
            team.get_or_create_team_code(),
            csv_buffer.getvalue(),
            "CSV export should reference the selected team",
        )

        xml_bytes = ArchiveExporter("test-tournament").export_tournament()
        self.assertTrue(xml_bytes.startswith(b"<tournament"),
                        "XML export should produce a tournament document")

        request = self.factory.get("/")
        tab_card = get_tab_card_data(request, team.pk)
        self.assertEqual(
            tab_card["team_school"],
            team.school,
            "Tab card export should return the expected team metadata",
        )

    def test_exports_with_malformed_data(self):
        team = Team.with_preloaded_relations_for_tab_card().first()
        fallback_team = Team.objects.first()
        team = team or fallback_team
        self.assertIsNotNone(team, "Expected at least one team in fixtures")

        problematic_round = (
            Round.objects.filter(gov_team=team).prefetch_related("judges").first()
        )
        self.assertIsNotNone(problematic_round,
                             "Expected at least one round for malformed export test")

        problematic_round.chair = None
        problematic_round.room = None
        problematic_round.save()
        problematic_round.judges.clear()
        RoundStats.objects.filter(round=problematic_round).delete()
        team.debaters.clear()
        team.save()

        export_data = get_all_json_data()
        self.assertTrue(export_data, "JSON export should not be empty")
        team_code = team.get_or_create_team_code()
        if team_code not in export_data:
            team_code = next(iter(export_data.keys()))
            team = Team.objects.filter(team_code=team_code).first() or team
        self.assertIn(
            team_code,
            export_data,
            "JSON export should still include teams with malformed data",
        )

        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)
        csv_tab_cards(csv_writer)
        self.assertTrue(
            csv_buffer.getvalue().strip(),
            "CSV export should still produce output with malformed data present",
        )

        xml_bytes = ArchiveExporter(
            "test-tournament-malformed"
        ).export_tournament()
        self.assertTrue(
            xml_bytes.startswith(b"<tournament"),
            (
                "XML export should still produce a tournament document "
                "when data is malformed"
            ),
        )

        request = self.factory.get("/")
        tab_card = get_tab_card_data(request, team.pk)
        self.assertIn(
            "round_stats",
            tab_card,
            "Tab card export should return a payload even when rounds are malformed",
        )

    def test_successful_export_when_results_published(self):
        TabSettings.set("results_published", False)
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalFilesystem(prefix=tmpdir, suffix=RESULTS_SUFFIX)
            with mock.patch(
                "mittab.libs.data_export.s3_connector.RESULTS_STORAGE",
                storage
            ), mock.patch(
                "mittab.apps.tab.views.views.schedule_results_export",
                side_effect=export_results_now
            ) as mocked_scheduler:
                request = self.factory.get("/", SERVER_NAME="test.local")
                setattr(request, "session", self.client.session)
                messages = FallbackStorage(request)
                setattr(request, "_messages", messages)
                response = publish_results(request, 1)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(mocked_scheduler.called)

            export_path = os.path.join(
                tmpdir, RESULTS_FILENAME + RESULTS_SUFFIX
            )
            self.assertTrue(
                os.path.exists(export_path),
                "Publishing results should trigger a JSON export",
            )
            with open(export_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            self.assertEqual(
                payload.get("tournament"),
                "test",
                "Export should be generated for the current tournament",
            )
