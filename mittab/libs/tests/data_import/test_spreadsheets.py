"""Exercise real file readers, layouts, rollback and import/export compatibility."""

from io import BytesIO

import openpyxl
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from mittab.apps.tab.auth_roles import (
    APDA_BOARD_GROUP_NAME,
    PRESET_CHECKIN_HELPER,
    PRESET_DATA_ENTRY_HELPER,
    apply_staff_permission_preset,
)
from mittab.apps.tab.models import (
    AuditEvent,
    Debater,
    Judge,
    JudgeExpectedCheckIn,
    Room,
    School,
    Scratch,
    TabSettings,
    Team,
)
from mittab.libs.data_export.roster_export import roster_rows
from mittab.libs.data_import.import_judges import import_judges
from mittab.libs.data_import.import_rooms import import_rooms
from mittab.libs.data_import.import_scratches import import_scratches
from mittab.libs.data_import.import_teams import import_teams
from mittab.libs.spreadsheets import LAYOUTS, ROSTER_KINDS, spreadsheet_response


def spreadsheet(kind, rows, extension="csv", headers=None):
    response = spreadsheet_response(kind, extension, headers or LAYOUTS[kind], rows)
    return SimpleUploadedFile(f"{kind}.{extension}", response.content)


@pytest.mark.django_db
class TestSpreadsheets(TestCase):
    fixtures = ["testing_empty"]

    def test_all_formats_round_trip_all_rosters(self):
        for extension in ("csv", "xls", "xlsx"):
            with self.subTest(extension=extension):
                TabSettings.set("tot_rounds", 2)
                team_row = [
                    "Team One",
                    "School One",
                    "Hybrid",
                    "Half Seed",
                    "Alice",
                    "Varsity",
                    "alice@example.com",
                    "123",
                    "Bob",
                    "Novice",
                    "bob@example.com",
                    "456",
                ]
                self.assertEqual(
                    import_teams(spreadsheet("teams", [team_row], extension)), []
                )
                self.assertEqual(
                    import_judges(
                        spreadsheet(
                            "judges",
                            [
                                [
                                    "Judge One",
                                    "judge@example.com",
                                    "8.75",
                                    "School One",
                                    "Hybrid",
                                    "yes",
                                    "",
                                ]
                            ],
                            extension,
                            LAYOUTS["judges"]
                            + [
                                "School 1",
                                "School 2",
                                "Round 1 expected",
                                "Round 2 expected",
                            ],
                        )
                    ),
                    [],
                )
                self.assertEqual(
                    import_rooms(spreadsheet("rooms", [["Hall", "9.5"]], extension)), []
                )
                self.assertEqual(
                    import_scratches(
                        spreadsheet(
                            "scratches", [["Team One", "Judge One", "tab"]], extension
                        )
                    ),
                    [],
                )
                exports = {
                    kind: spreadsheet(
                        kind, roster_rows(kind)[1], extension, roster_rows(kind)[0]
                    )
                    for kind in ROSTER_KINDS
                }
                Scratch.objects.all().delete()
                Team.objects.all().delete()
                Debater.objects.all().delete()
                Judge.objects.all().delete()
                Room.objects.all().delete()
                for kind, importer in [
                    ("teams", import_teams),
                    ("judges", import_judges),
                    ("rooms", import_rooms),
                    ("scratches", import_scratches),
                ]:
                    self.assertEqual(importer(exports[kind]), [], kind)
                self.assertEqual(Team.objects.get().seed, Team.HALF_SEED)
                self.assertEqual(Debater.objects.get(name="Alice").apda_id, 123)
                self.assertEqual(
                    Debater.objects.get(name="Bob").novice_status, Debater.NOVICE
                )
                self.assertEqual(Judge.objects.get().schools.count(), 2)
                self.assertEqual(JudgeExpectedCheckIn.objects.get().round_number, 1)
                self.assertEqual(
                    Scratch.objects.get().scratch_type, Scratch.TAB_SCRATCH
                )
                self.assertEqual(float(Room.objects.get().rank), 9.5)
                Scratch.objects.all().delete()
                Team.objects.all().delete()
                Debater.objects.all().delete()
                Judge.objects.all().delete()
                Room.objects.all().delete()

    def test_reordered_alias_headers_and_optional_columns(self):
        upload = spreadsheet(
            "judges",
            [["9.25", "j@example.com", "Jane", "yes", "NU"]],
            headers=["Judge rank", "EMAIL ADDRESS", "Name", "R1", "School 1"],
        )
        self.assertEqual(import_judges(upload), [])
        judge = Judge.objects.get()
        self.assertEqual(judge.name, "Jane")
        self.assertEqual(judge.email, "j@example.com")
        self.assertEqual(judge.schools.get().name, "NU")
        self.assertEqual(judge.expected_checkins.get().round_number, 1)

    def test_default_judge_order_retains_legacy_school_headers(self):
        TabSettings.set("tot_rounds", 1)
        upload = spreadsheet(
            "judges",
            [["Jane", "", "9", "NU", "yes"]],
            headers=["Judge", "Email", "Rank", "Affiliation", "Available"],
        )
        self.assertEqual(import_judges(upload), [])
        judge = Judge.objects.get()
        self.assertEqual(judge.schools.get().name, "NU")
        self.assertEqual(judge.expected_checkins.get().round_number, 1)

    def test_inference_fills_unrecognized_headers_after_named_columns(self):
        upload = spreadsheet(
            "judges",
            [["j@example.com", "9", "Jane"]],
            headers=["Contact", "Calibration", "Judge name"],
        )
        self.assertEqual(import_judges(upload), [])
        self.assertEqual(Judge.objects.get().email, "j@example.com")
        upload = spreadsheet("rooms", [["9", "Hall"]], headers=["Rank", "Location"])
        self.assertEqual(import_rooms(upload), [])
        self.assertEqual(Room.objects.get().name, "Hall")

    def test_headerless_files_preserve_first_row_and_infer_types(self):
        self.assertEqual(
            import_rooms(SimpleUploadedFile("rooms.csv", b"8.25,Hall\n9,Room Two\n")),
            [],
        )
        self.assertEqual(Room.objects.count(), 2)
        self.assertEqual(
            import_judges(
                SimpleUploadedFile("judges.csv", b"j@example.com,9.5,Jane\n")
            ),
            [],
        )
        self.assertEqual(Judge.objects.get().name, "Jane")

    def test_default_order_wins_for_ambiguous_text(self):
        upload = SimpleUploadedFile(
            "teams.csv", ("Team One,NU,,,Alice,,,123,Bob,,,456\n").encode()
        )
        self.assertEqual(import_teams(upload), [])
        self.assertEqual(Team.objects.get().name, "Team One")
        self.assertEqual(Team.objects.get().school.name, "NU")

    def test_reordered_teams_and_scratches(self):
        team = [
            "Team One",
            "NU",
            "",
            "full",
            "Alice",
            "",
            "a@example.com",
            "123",
            "Bob",
            "n",
            "b@example.com",
            "456",
        ]
        order = [6, 4, 11, 8, 1, 0, 3, 2, 9, 5, 7, 10]
        self.assertEqual(
            import_teams(
                spreadsheet(
                    "teams",
                    [[team[i] for i in order]],
                    headers=[LAYOUTS["teams"][i] for i in order],
                )
            ),
            [],
        )
        Judge.objects.create(name="Jane", rank=9)
        self.assertEqual(
            import_scratches(
                spreadsheet(
                    "scratches",
                    [["tab", "Jane", "Team One"]],
                    headers=["Type", "Judge", "Team"],
                )
            ),
            [],
        )
        self.assertEqual(Scratch.objects.get().scratch_type, Scratch.TAB_SCRATCH)

    def test_missing_and_ambiguous_columns(self):
        errors = import_judges(SimpleUploadedFile("judges.csv", b"9.5,Jane,NU\n"))
        self.assertIn('"Judge name"', errors[0])
        self.assertIn("unambiguous", errors[0])
        errors = import_teams(
            spreadsheet("teams", [["A", "NU"]], headers=["Team", "School"])
        )
        self.assertIn('"Debater 1 name"', errors[0])
        self.assertEqual(Team.objects.count(), 0)

    def test_detailed_errors_and_atomic_rollback_including_audits(self):
        user = get_user_model().objects.create_superuser(
            "spreadsheet-tester", "tab@example.com", "password"
        )
        original_audits = AuditEvent.objects.count()
        errors = import_judges(
            spreadsheet(
                "judges",
                [
                    ["Valid Judge", "valid@example.com", "9"],
                    ["Name that is much longer than thirty characters", "bad", "8"],
                ],
            ),
            created_by=user,
        )
        self.assertTrue(
            any(
                "Row 3:" in error
                and 'column "Judge name"' in error
                and "30 characters" in error
                for error in errors
            ),
            errors,
        )
        self.assertTrue(
            any('Value "bad" in column "Judge email"' in error for error in errors)
        )
        self.assertEqual(Judge.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), original_audits)

    def test_invalid_numbers_school_limits_and_physical_row_numbers(self):
        errors = import_rooms(
            SimpleUploadedFile("rooms.csv", b"Room name,Rank\n\n\nHall,NaN\n")
        )
        self.assertIn('Row 4: Value "NaN" in column "Rank"', errors[0])
        for value in ("Infinity", "nonsense", "1e999"):
            errors = import_judges(spreadsheet("judges", [["Jane", "", value]]))
            self.assertIn(f'Value "{value}" in column "Calibration"', errors[0])
        errors = import_judges(
            spreadsheet(
                "judges",
                [["Jane", "", "9", "X" * 51]],
                headers=LAYOUTS["judges"] + ["School 1"],
            )
        )
        self.assertIn('column "School"', errors[0])
        self.assertIn("50 characters", errors[0])
        self.assertEqual(School.objects.count(), 0)

    def test_invalid_status_and_expected_round_values(self):
        team = [
            "Team One",
            "NU",
            "",
            "",
            "Alice",
            "rookie",
            "",
            "123",
            "Bob",
            "",
            "",
            "456",
        ]
        errors = import_teams(spreadsheet("teams", [team]))
        self.assertIn('Value "rookie" in column "Debater 1 status"', errors[0])
        self.assertEqual(Team.objects.count(), 0)
        errors = import_judges(
            spreadsheet(
                "judges",
                [["Jane", "", "9", "maybe"]],
                headers=LAYOUTS["judges"] + ["Round 1 expected"],
            )
        )
        self.assertIn('Value "maybe" in column "Round 1 expected"', errors[0])
        self.assertEqual(Judge.objects.count(), 0)
        self.assertEqual(School.objects.count(), 0)

    def test_header_aliases_in_first_data_row_are_not_discarded(self):
        errors = import_rooms(SimpleUploadedFile("rooms.csv", b"Room,8\nName,9\n"))
        self.assertEqual(errors, [])
        self.assertEqual(
            set(Room.objects.values_list("name", flat=True)), {"Room", "Name"}
        )

    def test_bad_files_and_duplicate_headers(self):
        for name, content in [
            ("rooms.txt", b"a,b"),
            ("rooms.csv", b""),
            ("rooms.xlsx", b"broken"),
            ("rooms.xls", b"broken"),
            ("rooms.csv", b"\xff\xfe"),
            ("rooms.csv", b'"unterminated'),
            ("rooms.csv", b"Room name,Rank\n"),
        ]:
            errors = import_rooms(SimpleUploadedFile(name, content))
            self.assertTrue(errors, name)
        errors = import_rooms(
            SimpleUploadedFile("rooms.csv", b"Room,Room,Rank\nA,B,9\n")
        )
        self.assertIn("Duplicate column", errors[0])

    def test_excel_integer_ids_and_literal_formula_text(self):
        for extension in ("xls", "xlsx"):
            upload = spreadsheet("rooms", [["=Literal room", 9]], extension)
            self.assertEqual(import_rooms(upload), [])
            self.assertEqual(Room.objects.get().name, "=Literal room")
            Room.objects.all().delete()
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(LAYOUTS["teams"])
        sheet.append(
            ["Team One", "NU", "", "", "Alice", "", "", 123, "Bob", "", "", 456]
        )
        output = BytesIO()
        book.save(output)
        self.assertEqual(
            import_teams(SimpleUploadedFile("teams.xlsx", output.getvalue())), []
        )
        self.assertEqual(Debater.objects.get(name="Alice").apda_id, 123)

    def test_upload_errors_render_and_empty_upload_is_not_success(self):
        user = get_user_model().objects.create_superuser(
            "spreadsheet-tester", "tab@example.com", "password"
        )
        self.client.force_login(user)
        response = self.client.post(reverse("upload_data"), {})
        self.assertContains(response, "Choose at least one spreadsheet")
        response = self.client.post(
            reverse("upload_data"),
            {"room_file": spreadsheet("rooms", [["Hall", "invalid"]])},
        )
        self.assertContains(response, "column &quot;Rank&quot;")
        self.assertContains(response, "all room uploads aborted")
        self.assertEqual(Room.objects.count(), 0)
        response = self.client.post(
            reverse("upload_data"), {"room_file": SimpleUploadedFile("empty.csv", b"")}
        )
        self.assertContains(response, "The submitted file is empty.")
        self.assertContains(response, 'accept=".csv,.xls,.xlsx"')

    def test_export_permissions_and_allowlists(self):
        user = get_user_model().objects.create_superuser(
            "spreadsheet-tester", "tab@example.com", "password"
        )
        self.client.force_login(user)
        for kind in ROSTER_KINDS:
            for extension in ("csv", "xls", "xlsx"):
                url = reverse("export_roster", args=[kind, extension])
                self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(self.client.post(url).status_code, 405)
        for kind, extension in [("judges", "json"), ("schools", "csv")]:
            self.assertEqual(
                self.client.get(
                    reverse("export_roster", args=[kind, extension])
                ).status_code,
                400,
            )
        user.is_superuser = False
        user.save()
        apply_staff_permission_preset(user, PRESET_DATA_ENTRY_HELPER)
        self.assertEqual(
            self.client.get(
                reverse("export_roster", args=["rooms", "csv"])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("export_roster", args=["scratches", "csv"])
            ).status_code,
            403,
        )
        self.assertNotContains(
            self.client.get(reverse("upload_data")), "Scratches</td>"
        )
        apply_staff_permission_preset(user, PRESET_CHECKIN_HELPER)
        self.assertIn(
            self.client.get(
                reverse("export_roster", args=["judges", "csv"])
            ).status_code,
            [302, 403],
        )
        user.is_superuser = True
        user.save()
        user.groups.add(Group.objects.get_or_create(name=APDA_BOARD_GROUP_NAME)[0])
        for kind in ROSTER_KINDS:
            for method in ("get", "post"):
                self.assertIn(
                    getattr(self.client, method)(
                        reverse("export_roster", args=[kind, "csv"])
                    ).status_code,
                    [302, 403],
                )
