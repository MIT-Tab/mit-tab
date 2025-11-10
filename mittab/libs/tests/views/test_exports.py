import csv
import io

import pytest
from django.test import TestCase, RequestFactory

from mittab.apps.tab.models import Team, Round, RoundStats, TabSettings
from mittab.libs.data_export.tab_card import (
    get_all_json_data,
    csv_tab_cards,
    get_tab_card_data,
)
from mittab.libs.data_export.xml_archive import ArchiveExporter


@pytest.mark.django_db(transaction=True)
class TestExports(TestCase):
    fixtures = ["testing_finished_db"]

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        TabSettings.set("cur_round", 2)
        TabSettings.set("tot_rounds", 5)

    def test_exports_with_standard_data(self):
        team = Team.objects.with_preloaded_relations_for_tab_card().first()
        self.assertIsNotNone(team, "Expected at least one team in fixtures")

        export_data = get_all_json_data()
        self.assertIn(
            team.get_or_create_team_code(),
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
        team = Team.objects.with_preloaded_relations_for_tab_card().first()
        self.assertIsNotNone(team, "Expected at least one team in fixtures")

        problematic_round = (
            Round.objects.filter(gov_team=team).prefetch_related("judges").first()
        )
        self.assertIsNotNone(problematic_round,
                             "Expected at least one round for malformed export test")

        problematic_round.chair = None
        problematic_round.room = None
        problematic_round.opp_team = None
        problematic_round.save()
        problematic_round.judges.clear()
        RoundStats.objects.filter(round=problematic_round).delete()
        team.debaters.clear()
        team.save()

        export_data = get_all_json_data()
        self.assertIn(
            team.get_or_create_team_code(),
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

        xml_bytes = ArchiveExporter("test-tournament-malformed").export_tournament()
        self.assertTrue(
            xml_bytes.startswith(b"<tournament"),
            "XML export should still produce a tournament document when data is malformed",
        )

        request = self.factory.get("/")
        tab_card = get_tab_card_data(request, team.pk)
        self.assertIn(
            "round_stats",
            tab_card,
            "Tab card export should return a payload even when rounds are malformed",
        )
