"""Compare the Rust HTTP server with Django over a shared disposable test DB."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import http.client
from http.cookies import SimpleCookie
import json
import os
import random
import re
from pathlib import Path
import subprocess
from urllib.parse import urlencode

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import caches
from django.db import connection, transaction
from django.test import Client
from django.utils import timezone

from mittab.apps.registration.models import InfoLink, InfoText, RegistrationConfig

from mittab.apps.tab.models import (
    AuditEvent, BreakingTeam, Bye, CheckIn, Debater, Judge, JudgeExpectedCheckIn, Motion, Outround, PublishedTeamPlacement, Round, RoundStats,
    NoShow, PublicRosterChange, Room, RoomCheckIn, RoomTag, School, Scratch, TabSettings, Team,
)

from mittab.libs import assign_judges, tab_logic
from mittab.libs.cacheing import cache_logic

COUNTS = "/api/debater-counts"
DEBATERS = "/api/new-debater-data"
SCHOOLS = "/api/new-schools"
LIVE = "/api/live-results"
IMPORT = "/api/import-results"
PLACEMENTS = (
    "/api/varsity-team-placements", "/api/novice-team-placements", "/api/non-placing-teams",
)
API_PATHS = (COUNTS, DEBATERS, SCHOOLS, *PLACEMENTS)


@contextmanager
def rust_server(session_key_configured=True, overrides=None):
    db = connection.settings_dict
    assert db["HOST"] in ("localhost", "127.0.0.1", "::1", "")
    assert db["NAME"].startswith("test_"), "Rust parity requires a disposable Django test DB"
    binary = Path(__file__).parents[1] / "target" / "debug" / "mittab-server"
    environment = {
        **os.environ,
        "MITTAB_RUST_LISTEN": "127.0.0.1:0",
        "AWS_SES_ACCESS_KEY_ID": "",
        "AWS_SES_SECRET_ACCESS_KEY": "",
        "AWS_ACCESS_KEY_ID": "",
        "AWS_SECRET_ACCESS_KEY": "",
        "AWS_SESSION_TOKEN": "",
        "AWS_MAILMANAGER_ADDRESS_LIST": "",
        "MITTAB_PUBLIC_URL": "",
        "MITTAB_SES_ENDPOINT": "",
        "MYSQL_DATABASE": db["NAME"],
        "MYSQL_HOST": db["HOST"] or "127.0.0.1",
        "MYSQL_PORT": db["PORT"] or "3306",
        "MYSQL_USER": db["USER"],
        "MYSQL_PASSWORD": db["PASSWORD"],
        "MITTAB_DJANGO_SECRET_KEY": settings.SECRET_KEY if session_key_configured else "",
        **(overrides or {}),
    }
    assert binary.is_file(), "Build mittab-server before running the Rust HTTP parity suite"
    with subprocess.Popen([binary], env=environment, stderr=subprocess.PIPE, text=True) as process:
        try:
            line = process.stderr.readline()
            prefix = "MIT-Tab listening on 127.0.0.1:"
            assert line.startswith(prefix), "Rust server failed during startup"
            yield int(line.split(":")[-1])
        finally:
            process.terminate()
            process.wait(timeout=10)


def request(port, path, *, method="GET", cookie=None, forwarded_proto=None, form=None,
            extra_headers=None):
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = dict(extra_headers or {})
        if cookie is not None:
            headers["Cookie"] = f"sessionid={cookie}"
        if forwarded_proto is not None:
            headers["X-Forwarded-Proto"] = forwarded_proto
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        client.request(method, path, headers=headers,
                       body=None if form is None else urlencode(form))
        reply = client.getresponse()
        return reply.status, reply.headers, reply.read()
    finally:
        client.close()


@pytest.fixture(autouse=True)
def isolate_tournament_settings(django_db_setup, django_db_blocker):
    caches["default"].clear()
    caches["filesystem"].clear()
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        assert connection.settings_dict["NAME"].startswith("test_")
        cursor.execute(
            """
            SELECT TABLE_NAME FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN ('mittab_rust_session', 'mittab_public_logo',
                                 'mittab_staff_invite_token', 'mittab_registration_private_email')
            """
        )
        for (table,) in cursor.fetchall():
            # Django's flush knows only about Django-owned tables.
            cursor.execute(f"DELETE FROM `{table}`")
    yield
    caches["default"].clear()
    caches["filesystem"].clear()


@pytest.mark.django_db(transaction=True)
def test_public_roster_apis_match_django():
    first_school = School.objects.create(name="Example North", apda_id=-1)
    second_school = School.objects.create(name="Example South", apda_id=47)
    teams = [
        Team.objects.create(name=f"Team {index}", school=school, seed=Team.UNSEEDED)
        for index, school in enumerate((first_school, second_school, first_school))
    ]
    varsity = Debater.objects.create(name="Example Varsity", novice_status=0, apda_id=-1)
    novice = Debater.objects.create(name="Example Novice", novice_status=1, apda_id=22)
    cut_off = Debater.objects.create(name="Example Cutoff", novice_status=1, apda_id=-1)
    teams[0].debaters.add(varsity, novice)
    teams[1].debaters.add(varsity)
    teams[2].debaters.add(cut_off)
    for number in range(1, 4):
        Round.objects.create(round_number=number, gov_team=teams[0], opp_team=teams[1])
    Round.objects.create(round_number=4, gov_team=teams[2], opp_team=teams[0])

    client = Client()
    with rust_server() as port:
        for path in API_PATHS:
            expected = client.get(path)
            status, _, raw = request(port, path)
            assert status == expected.status_code == 200
            actual = json.loads(raw)
            reference = expected.json()
            if path in (DEBATERS, SCHOOLS):
                key = next(iter(reference))
                reference[key].sort(key=repr)
                actual[key].sort(key=repr)
            assert actual == reference
            assert request(port, path, method="HEAD")[2] == b""
            assert request(port, path, method="POST")[0] == 405


@pytest.mark.django_db(transaction=True)
def test_board_api_reads_fail_closed_before_and_after_access_opens():
    group = Group.objects.create(name="APDA Board")
    user = get_user_model().objects.create_superuser(username="board-test", password="synthetic")
    user.groups.add(group)
    tab_client = Client()
    tab_client.force_login(user)
    cookie = tab_client.cookies["sessionid"].value

    school = School.objects.create(name="Example")
    first = Team.objects.create(name="First team", school=school, seed=Team.UNSEEDED)
    second = Team.objects.create(name="Second team", school=school, seed=Team.UNSEEDED)
    TabSettings.set("tot_rounds", 5)
    rounds = [
        Round.objects.create(
            round_number=5, gov_team=first, opp_team=second,
            victor=Round.GOV if index < 3 else Round.UNKNOWN,
        )
        for index in range(5)
    ]
    with rust_server() as port:
        for phase in ("below threshold", "at threshold", "outrounds exist"):
            if phase == "at threshold":
                Round.objects.filter(pk=rounds[3].pk).update(victor=Round.OPP)
            elif phase == "outrounds exist":
                Round.objects.filter(round_number=5).update(victor=Round.UNKNOWN)
                Outround.objects.create(gov_team=first, opp_team=second, num_teams=2)
            for path in API_PATHS:
                expected = tab_client.get(path)
                status, headers, body = request(port, path, cookie=cookie)
                assert status == expected.status_code
                assert headers["cache-control"] == "private, no-store"
                if status == 403:
                    assert body == expected.content
                else:
                    assert headers["location"] == expected["Location"] == "/403/"
                assert request(port, path)[0] == 200
                assert request(port, path, method="HEAD", cookie=cookie)[0] == status


@pytest.mark.django_db(transaction=True)
def test_missing_session_signing_key_does_not_remove_staff_restrictions():
    user = get_user_model().objects.create_user(username="staff-test", password="synthetic", is_staff=True)
    client = Client()
    client.force_login(user)
    cookie = client.cookies["sessionid"].value
    with rust_server() as port:
        for path in API_PATHS:
            assert request(port, path, cookie=cookie)[0] == client.get(path).status_code == 302
    with rust_server(session_key_configured=False) as port:
        status, _, body = request(port, COUNTS, cookie=cookie)
        assert status == 503
        assert body == b"Unable to complete this request."
        assert request(port, COUNTS)[0] == 200


@pytest.mark.django_db(transaction=True)
def test_legacy_placements_match_django_with_forfeits_and_byes():
    school = School.objects.create(name="Examples")
    teams = [
        Team.objects.create(name=f"Example {i}", seed=Team.UNSEEDED, school=school,
                            break_preference=Team.NOVICE if i >= 4 else Team.VARSITY)
        for i in range(8)
    ]
    for index, team in enumerate(teams[:7]):
        team.debaters.add(Debater.objects.create(name=f"Speaker {index}", novice_status=index % 2))
    Outround.objects.create(
        gov_team=teams[0], opp_team=teams[1], num_teams=4, victor=Outround.GOV,
    )
    Outround.objects.create(
        gov_team=teams[0], opp_team=teams[2], num_teams=2, victor=Outround.OPP_VIA_FORFEIT,
    )
    Outround.objects.create(
        gov_team=teams[4], opp_team=teams[5], num_teams=2,
        type_of_round=Outround.NOVICE, victor=Outround.OPP,
    )
    Outround.objects.create(
        gov_team=teams[6], opp_team=teams[7], num_teams=4,
        type_of_round=Outround.NOVICE, victor=Outround.UNKNOWN,
    )
    for index, victor in enumerate((Round.GOV, Round.ALL_WIN, Round.GOV_VIA_FORFEIT)):
        Round.objects.create(
            round_number=index + 1, gov_team=teams[3], opp_team=teams[7], victor=victor,
        )
    Bye.objects.create(bye_team=teams[3], round_number=4)
    client = Client()
    with rust_server() as port:
        for path in PLACEMENTS:
            expected = client.get(path)
            status, _, body = request(port, path)
            assert status == expected.status_code == 200
            assert json.loads(body) == expected.json()


@pytest.mark.django_db(transaction=True)
def test_speaker_awards_match_legacy_scoring_and_publication_gates():
    school = School.objects.create(name="Examples")
    teams = [
        Team.objects.create(name=f"Example {i}", seed=Team.UNSEEDED, school=school)
        for i in range(2)
    ]
    speakers = [
        Debater.objects.create(name=f"Speaker {i}", novice_status=i % 2, apda_id=i + 10)
        for i in range(4)
    ]
    for index, team in enumerate(teams):
        team.debaters.add(*speakers[2 * index:2 * index + 2])

    TabSettings.set("cur_round", 4)
    TabSettings.set("tot_rounds", 3)
    rounds = [
        Round.objects.create(round_number=i + 1, gov_team=teams[0], opp_team=teams[1],
                             victor=victor)
        for i, victor in enumerate((Round.GOV, Round.GOV_VIA_FORFEIT, Round.OPP))
    ]
    for index, round_obj in enumerate(rounds):
        for pos, debater in enumerate(speakers):
            RoundStats.objects.create(
                debater=debater, round=round_obj,
                speaks=Decimal(24) + Decimal(f"0.{index}{pos}13"),
                ranks=Decimal(4 - pos),
            )
    Bye.objects.create(bye_team=teams[0], round_number=2)
    judge = Judge.objects.create(name="Example Chair", rank=3)
    last_judge = Judge.objects.create(name="Example Wing", rank=1)
    Round.objects.filter(pk=rounds[0].pk).update(chair=judge)
    rounds[0].judges.add(last_judge)
    paths = ("/api/varsity-speaker-awards", "/api/novice-speaker-awards")
    client = Client()

    def check(port, code):
        compare_live(port, client, completed=code == 200)
        caches["default"].clear()
        for path in paths:
            status, _, body = request(port, path)
            expected = client.get(path)
            assert status == expected.status_code == code
            assert json.loads(body) == expected.json()

    with rust_server() as port:
        check(port, 423)
        compare_import(port, client, ready=False)
        old = timezone.now() - timedelta(hours=4)
        Round.objects.filter(pk=rounds[-1].pk).update(result_entered_at=old)
        check(port, 200)
        compare_import(port, client, ready=True)
        TabSettings.set("speaker_rankings_use_single_adjusted", 1)
        check(port, 200)
        Round.objects.filter(pk=rounds[-1].pk).update(victor=Round.UNKNOWN)
        check(port, 423)
        TabSettings.set("nov_teams_visible", 4)
        bye_team = Team.objects.create(name="Opening Bye", seed=Team.UNSEEDED, school=school)
        BreakingTeam.objects.create(team=bye_team, type_of_team=Team.NOVICE)
        outround = Outround.objects.create(
            gov_team=teams[0], opp_team=teams[1], num_teams=4,
            type_of_round=Outround.NOVICE,
        )
        outround.judges.add(judge, last_judge)
        compare_live(port, client, completed=True, expected_placement_count=3)
        compare_import(port, client, ready=True)
        check(port, 200)
        TabSettings.set("nov_teams_visible", 256)
        outround.delete()
        check(port, 200)



def compare_live(port, client, *, completed, expected_placement_count=None):
    caches["default"].clear()
    status, _, raw = request(port, LIVE, forwarded_proto="https")
    assert status == 200
    actual = json.loads(raw)
    assert actual["availability"]["speakers"] is completed
    if expected_placement_count is not None:
        assert PublishedTeamPlacement.objects.count() == expected_placement_count
    expected = client.get(LIVE, HTTP_HOST=f"127.0.0.1:{port}", HTTP_X_FORWARDED_PROTO="https")
    assert expected.status_code == 200
    expected = expected.json()
    for payload in (actual, expected):
        generated = datetime.fromisoformat(payload.pop("generated_at"))
        assert abs((generated - timezone.now()).total_seconds()) < 5
    assert actual == expected



def compare_import(port, client, *, ready):
    status, _, raw = request(port, IMPORT)
    expected = client.get(IMPORT, HTTP_HOST=f"127.0.0.1:{port}")
    assert status == expected.status_code == 200
    actual = json.loads(raw)
    expected = expected.json()
    for value in (actual, expected):
        value.pop("generated_at")
        assert value["import_ready"] is ready
        if ready:
            bundle = value["bundle"]
            bundle.pop("exported_at")
            assert bundle["source"] == "mit_tab_black_rod_bundle"
            assert all(set(judge) == {"original_name", "is_chair"}
                       for row in bundle["rounds"] for judge in row["judges"])
        else:
            assert value["bundle"] is None
    assert actual == expected


@pytest.mark.django_db(transaction=True)
def test_superuser_setup_gate_precedes_api_reads():
    user = get_user_model().objects.create_superuser(username="tab-test", password="synthetic")
    client = Client()
    client.force_login(user)
    cookie = client.cookies["sessionid"].value
    TabSettings.set("start_tournament_wizard_completed", 0)
    with rust_server() as port:
        for path in (COUNTS, LIVE, IMPORT):
            expected = client.get(path)
            status, headers, _ = request(port, path, cookie=cookie)
            assert status == expected.status_code == 302
            assert headers["location"] == expected["Location"] == "/setup/start-tournament/"
            assert request(port, path)[0] == 200


def rust_command(command, database, *, tab_password=None):
    db = connection.settings_dict
    assert db["HOST"] in ("localhost", "127.0.0.1", "::1", "")
    assert database.startswith("test_")
    binary = Path(__file__).parents[1] / "target" / "debug" / "mittab-server"
    environment = {
        **os.environ,
        "MYSQL_DATABASE": database,
        "MYSQL_HOST": db["HOST"] or "127.0.0.1",
        "MYSQL_PORT": db["PORT"] or "3306",
        "MYSQL_USER": db["USER"],
        "MYSQL_PASSWORD": db["PASSWORD"],
        "TAB_PASSWORD": tab_password or "",
        "ENTRY_PASSWORD": "synthetic-test-entry",
        "BOARD_PASSWORD": "synthetic-test-board",
    }
    return subprocess.run(
        [binary, command], env=environment, capture_output=True, text=True, timeout=30,
        check=False,
    )


@pytest.mark.django_db(transaction=True)
def test_native_initialization_matches_django_mysql_schema_without_resetting_users():
    from django.contrib.auth.hashers import check_password
    from uuid import uuid4

    legacy = connection.settings_dict["NAME"]
    assert legacy.startswith("test_")
    database = f"{legacy[:43]}_native_{uuid4().hex[:8]}"
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
    try:
        init_password = "synthetic-test-tab"
        missing = rust_command("init", database)
        assert missing.returncode == 1
        assert "TAB_PASSWORD" in missing.stderr
        prepared = rust_command("migrate", database)
        assert prepared.returncode == 0, prepared.stderr
        initialized = rust_command("init", database, tab_password=init_password)
        assert initialized.returncode == 0, initialized.stderr

        with connection.cursor() as cursor:
            schema = """
                SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
                FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s
            """
            cursor.execute(schema, (legacy,))
            expected = set(cursor.fetchall())
            cursor.execute(schema, (database,))
            assert expected <= set(cursor.fetchall())
            cursor.execute(f"SELECT username, password, is_superuser FROM `{database}`.auth_user")
            accounts = {name: (hashed, elevated) for name, hashed, elevated in cursor.fetchall()}
            assert set(accounts) == {"tab", "entry", "board"}
            assert check_password(init_password, accounts["tab"][0])
            assert check_password("synthetic-test-entry", accounts["entry"][0])
            assert check_password("synthetic-test-board", accounts["board"][0])
            assert accounts["tab"][1] == 1
            assert accounts["board"][1] == 0
            cursor.execute(f"""
                SELECT u.username FROM `{database}`.auth_user u
                JOIN `{database}`.auth_user_groups m ON m.user_id = u.id
                JOIN `{database}`.auth_group g ON g.id = m.group_id
                WHERE g.name = 'APDA Board'
            """)
            assert cursor.fetchall() == (("board",),)
            cursor.execute(f"INSERT INTO `{database}`.tab_school (name, apda_id) VALUES ('Saved', 3)")
        repeated = rust_command("init", database, tab_password="ignore-test-password")
        assert repeated.returncode == 0, repeated.stderr
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT password FROM `{database}`.auth_user WHERE username = 'tab'")
            assert cursor.fetchone()[0] == accounts["tab"][0]
            cursor.execute(f"SELECT name FROM `{database}`.tab_school")
            assert cursor.fetchall() == (("Saved",),)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE `{database}`")


@pytest.mark.django_db(transaction=True)
def test_native_migration_adopts_existing_latest_django_schema_without_resetting_tournament():
    database = connection.settings_dict["NAME"]
    school = School.objects.create(name="Keep School", apda_id=58)
    initialized = rust_command("init", database)
    assert initialized.returncode == 0, initialized.stderr
    # This sentinel has no users, teams, or settings. It must still keep the
    # old tournament database intact even when initialization is explicitly requested.
    assert School.objects.get(pk=school.pk).apda_id == 58
    prepared = rust_command("migrate", database)
    assert prepared.returncode == 0, prepared.stderr
    TabSettings.set("cur_round", 4)
    repeated = rust_command("init", database)
    assert repeated.returncode == 0, repeated.stderr
    assert TabSettings.get("cur_round") == 4
    assert School.objects.get(pk=school.pk).apda_id == 58


def load_cookies(headers):
    cookies = SimpleCookie()
    for header in headers.get_all("Set-Cookie", []):
        cookies.load(header)
    return cookies


def sign_in(port, username, password, *, next_path="", origin=None, csrf_valid=True):
    status, headers, body = request(port, "/public/login/")
    assert status == 200
    assert b"Tab Staff Login" in body
    cookie = load_cookies(headers)["csrftoken"].value
    request_headers = {"Cookie": f"csrftoken={cookie}"}
    if origin is not None:
        request_headers["Origin"] = origin
    form = {
        "username": username, "password": password, "next": next_path,
        "csrfmiddlewaretoken": cookie if csrf_valid else "invalid-test-token",
    }
    return request(port, "/public/login/", method="POST", form=form,
                   extra_headers=request_headers)


@pytest.mark.django_db(transaction=True)
def test_native_staff_login_honors_csrf_role_and_session_revocation():
    database = connection.settings_dict["NAME"]
    migrated = rust_command("migrate", database)
    assert migrated.returncode == 0, migrated.stderr
    user = get_user_model().objects.create_superuser(username="native-tab", password="synthetic")
    TabSettings.set("start_tournament_wizard_completed", 0)
    with rust_server() as port:
        assert sign_in(port, user.username, "synthetic", csrf_valid=False)[0] == 403
        cross_origin = sign_in(port, user.username, "synthetic", origin="https://example.invalid")
        assert cross_origin[0] == 403
        invalid = sign_in(port, user.username, "wrong")
        assert invalid[0] == 200
        assert "sessionid" not in load_cookies(invalid[1])
        status, headers, _ = sign_in(port, user.username, "synthetic",
                                     origin=f"http://127.0.0.1:{port}")
        assert status == 302
        assert headers["Location"] == "/setup/start-tournament/"
        first = load_cookies(headers)["sessionid"]
        assert first["httponly"] and first["samesite"] == "Lax"
        assert request(port, COUNTS, cookie=first.value)[1]["Location"] == "/setup/start-tournament/"
        TabSettings.set("start_tournament_wizard_completed", 1)
        status, headers, _ = sign_in(port, user.username, "synthetic", next_path="//example.invalid")
        assert status == 302
        assert headers["Location"] == "/setup/tournament-todo/"
        second = load_cookies(headers)["sessionid"].value
        assert request(port, COUNTS, cookie=second)[0] == 200
        status, headers, _ = request(port, "/accounts/logout/", cookie=second)
        assert status == 302
        assert load_cookies(headers)["sessionid"]["max-age"] == "0"
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM mittab_rust_session WHERE token_hash = UNHEX(%s)",
                (sha256(second.encode()).hexdigest(),),
            )
            assert cursor.fetchone()[0] == 0

        user.set_password("rotated-synthetic")
        user.save(update_fields=["password"])
        TabSettings.set("start_tournament_wizard_completed", 0)
        status, headers, _ = request(port, COUNTS, cookie=first.value)
        assert status == 200  # A revoked session sees only the anonymous response.
        assert "Location" not in headers


@pytest.mark.django_db(transaction=True)
def test_native_board_login_opens_on_outcomes_and_retains_board_restrictions():
    migrated = rust_command("migrate", connection.settings_dict["NAME"])
    assert migrated.returncode == 0, migrated.stderr
    group = Group.objects.create(name="APDA Board")
    user = get_user_model().objects.create_superuser(username="native-board", password="synthetic")
    user.groups.add(group)
    school = School.objects.create(name="Examples")
    teams = [
        Team.objects.create(name=f"Example {i}", seed=Team.UNSEEDED, school=school)
        for i in range(2)
    ]
    TabSettings.set("tot_rounds", 5)
    rounds = [
        Round.objects.create(
            round_number=5, gov_team=teams[0], opp_team=teams[1],
            victor=Round.GOV if i < 3 else Round.UNKNOWN,
        )
        for i in range(5)
    ]
    with rust_server() as port:
        status, headers, _ = sign_in(port, user.username, "synthetic")
        assert status == 200
        assert "sessionid" not in load_cookies(headers)
        Round.objects.filter(pk=rounds[3].pk).update(victor=Round.OPP)
        status, headers, _ = sign_in(port, user.username, "synthetic")
        assert status == 302
        assert headers["Location"] == "/apda-board/"
        session = load_cookies(headers)["sessionid"].value
        for path in (COUNTS, LIVE, IMPORT):
            status, headers, _ = request(port, path, cookie=session)
            assert status == 302
            assert headers["Location"] == "/403/"
        Round.objects.filter(pk=rounds[3].pk).update(victor=Round.UNKNOWN)
        assert request(port, LIVE, cookie=session)[0] == 403
        assert request(port, "/admin/logout/", cookie=session)[1]["Location"] == "/"


@pytest.mark.django_db(transaction=True)
def test_native_motion_workflow_add_edit_and_publish():
    migrated = rust_command("migrate", connection.settings_dict["NAME"])
    assert migrated.returncode == 0, migrated.stderr
    user = get_user_model().objects.create_superuser(username="motions-tab", password="synthetic")
    TabSettings.set("tot_rounds", 5)
    TabSettings.set("motions_enabled", 1)
    with rust_server() as port:
        login, headers, _ = sign_in(port, user.username, "synthetic", next_path="/motions/")
        assert login == 302
        assert headers["Location"] == "/motions/"
        cookies = load_cookies(headers)
        cookie = cookies["sessionid"].value
        csrf = cookies["csrftoken"].value
        jar = {"Cookie": f"sessionid={cookie}; csrftoken={csrf}"}

        status, _, page = request(port, "/motions/", extra_headers=jar)
        assert status == 200
        assert b"Manage Motions" in page
        assert b"Varsity Quarterfinals" in page
        assert b"Add Motion" in page
        assert b"No Motions Available" in request(port, "/public/motions/")[2]

        def post(path, form=None, *, token=csrf):
            body = {"csrfmiddlewaretoken": token, **(form or {})}
            return request(port, path, method="POST", extra_headers=jar, form=body)

        data = {
            "round_selection": "inround_3",
            "info_slide": "Context <em>text</em>",
            "motion_text": "THW Test a Motion",
        }
        assert post("/motions/add/", data, token="wrong")[0] == 403
        assert not Motion.objects.exists()
        assert post("/motions/add/", data)[1]["Location"] == "/motions/"
        motion = Motion.objects.get()
        assert motion.round_number == 3
        assert not motion.is_published
        assert b"THW Test a Motion" not in request(port, "/public/motions/")[2]

        edit = f"/motions/{motion.pk}/edit/"
        assert b"Edit Motion for Round 3" in request(port, edit, extra_headers=jar)[2]
        edited = {**data, "round_selection": "outround_0_8", "motion_text": "THW Test a New Motion"}
        invalid = post(edit, {**edited, "motion_text": ""})
        assert invalid[0] == 200
        assert b"Motion text is required." in invalid[2]
        assert post(edit, edited)[0] == 302
        motion.refresh_from_db()
        assert motion.round_number is None
        assert motion.outround_type == 0
        assert motion.num_teams == 8
        assert post("/motions/publish-all/")[0] == 302
        page = request(port, "/public/motions/")[2]
        assert b"Varsity Quarterfinals" in page
        assert b"Context <em>" not in page
        assert "Context <em>text</em>" in unescape(page.decode())
        assert b"THW Test a New Motion" in page
        assert post("/motions/unpublish-all/")[0] == 302
        assert b"THW Test a New Motion" not in request(port, "/public/motions/")[2]
        toggle = f"/motions/{motion.pk}/toggle-published/"
        assert post(toggle)[0] == 302
        motion.refresh_from_db()
        assert motion.is_published
        delete = f"/motions/{motion.pk}/delete/"
        assert request(port, delete, extra_headers=jar)[0] == 302
        assert Motion.objects.filter(pk=motion.pk).exists()
        assert post(delete)[0] == 302
        assert not Motion.objects.filter(pk=motion.pk).exists()
        TabSettings.set("motions_enabled", 0)
        assert request(port, "/public/motions/")[1]["Location"] == "/public/access-error/"


@pytest.mark.django_db(transaction=True)
def test_native_board_motions_expose_only_published_read_access():
    group = Group.objects.create(name="APDA Board")
    user = get_user_model().objects.create_superuser(username="motions-board", password="synthetic")
    user.groups.add(group)
    client = Client()
    client.force_login(user)
    cookie = client.cookies["sessionid"].value
    TabSettings.set("tot_rounds", 5)
    TabSettings.set("motions_enabled", 1)
    draft = Motion.objects.create(round_number=1, motion_text="Private draft")
    public = Motion.objects.create(round_number=2, motion_text="Public motion", is_published=True)
    with rust_server() as port:
        assert request(port, "/public/motions/", cookie=cookie)[0] == 403
        school = School.objects.create(name="Examples")
        teams = [
            Team.objects.create(name=f"Example {i}", seed=Team.UNSEEDED, school=school)
            for i in range(2)
        ]
        Outround.objects.create(gov_team=teams[0], opp_team=teams[1], num_teams=2)
        status, headers, page = request(port, "/public/motions/", cookie=cookie)
        assert status == 200
        assert headers["cache-control"] == "private, no-store"
        assert b"Public motion" in page and b"Private draft" not in page
        assert request(port, "/public/motions/", method="HEAD", cookie=cookie)[2] == b""
        assert request(port, "/motions/", cookie=cookie)[1]["Location"] == "/403/"
        for path in (f"/motions/{draft.pk}/edit/", f"/motions/{public.pk}/delete/",
                     "/motions/add/", "/motions/publish-all/", "/motions/unpublish-all/"):
            response = request(port, path, method="POST", cookie=cookie, form={})
            assert response[0] == 302
            assert response[1]["Location"] == "/403/"
        assert request(port, "/public/motions/", method="POST", cookie=cookie, form={})[0] in (302, 405)
        draft.refresh_from_db()
        public.refresh_from_db()
        assert not draft.is_published and public.is_published


@pytest.mark.django_db(transaction=True)
def test_native_public_rosters_board_projection_and_expected_records():
    group = Group.objects.create(name="APDA Board")
    account = get_user_model().objects.create_superuser(username="roster-board", password="synthetic")
    account.groups.add(group)
    client = Client()
    client.force_login(account)
    cookie = client.cookies["sessionid"].value

    school = School.objects.create(name="Public School")
    first = Team.objects.create(name="Public Team A", seed=Team.FREE_SEED, school=school)
    second = Team.objects.create(name="Public Team B", seed=Team.UNSEEDED, school=school, checked_in=False)
    debater = Debater.objects.create(name="Debater Hidden By Settings", novice_status=Debater.NOVICE)
    first.debaters.add(debater)
    judge = Judge.objects.create(name="Public Judge", rank=2)
    judge.schools.add(school)
    JudgeExpectedCheckIn.objects.create(judge=judge, round_number=2)
    Outround.objects.create(gov_team=first, opp_team=second, num_teams=2)
    TabSettings.set("tot_rounds", 5)
    teams = "/public/teams/"
    judges = "/public/judges/"
    records = "/public/teams/expected-records/"
    PublicRosterChange.objects.create(
        list_kind="teams", message="Sensitive roster entry suppressed by name visibility",
        involves_debaters=True,
    )
    PublicRosterChange.objects.create(
        list_kind="teams", message="Published Team Check-In Change", involves_debaters=False,
    )

    with rust_server() as port:
        for path in (teams, judges, records):
            assert request(port, path)[1]["Location"] == "/public/access-error/"
        TabSettings.set("teams_public", 1)
        TabSettings.set("judges_public", 1)
        TabSettings.set("debaters_public", 0)
        rendered = {path: request(port, path, cookie=cookie)[2] for path in (teams, judges, records)}
        assert b"Public Team A" in rendered[teams]
        assert b"Published Team Check-In Change" in rendered[teams]
        assert b"Debater Hidden By Settings" not in rendered[teams]
        assert b"Sensitive roster entry" not in rendered[teams]
        assert b"Public Judge" in rendered[judges]
        assert b'R2">' in rendered[judges] and "✔".encode() in rendered[judges]
        assert b"2 total teams" in rendered[records]
        assert b"1 checked in teams" in request(port, records + "?teams=checked-in", cookie=cookie)[2]
        assert b"Round 5" in rendered[records]

        Scratch.objects.create(team=first, judge=judge, scratch_type=Scratch.TEAM_SCRATCH)
        Judge.objects.filter(pk=judge.pk).update(rank=9, wing_only=True)
        Debater.objects.filter(pk=debater.pk).update(name="Different private name")
        for path in (teams, judges, records):
            assert request(port, path, cookie=cookie)[2] == rendered[path]

        TabSettings.set("debaters_public", 1)
        visible = request(port, teams, cookie=cookie)[2]
        assert b"Different private name" in visible
        assert b"Sensitive roster entry" in visible
        TabSettings.set("teams_public", 0)
        assert request(port, records, cookie=cookie)[1]["Location"] == "/public/access-error/"


class HomeLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tiles = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and "tile" in attributes.get("class", "").split():
            self.tiles.append(attributes["href"])


def home_links(html):
    parser = HomeLinks()
    parser.feed(html.decode())
    return parser.tiles


@pytest.mark.django_db(transaction=True)
def test_native_public_home_matches_tournament_phase_and_release_order():
    client = Client()
    config = RegistrationConfig.get_or_create_active()
    config.allow_new_registrations = True
    config.save()
    InfoText.objects.create(title="Food", text="Outside the main room")
    InfoLink.objects.create(title="Tournament guide", url="https://example.invalid/guide")
    TabSettings.set("tournament_name", "Native Tournament")
    TabSettings.set("tot_rounds", 5)
    TabSettings.set("motions_enabled", 1)
    TabSettings.set("teams_public", 1)
    TabSettings.set("judges_public", 1)
    TabSettings.set("tournament_director", "Example Director\n\nAnother Director")

    def compare(port, registration, status_text):
        caches["default"].clear()
        caches["public"].clear()
        status, _, page = request(port, "/public/")
        expected = client.get("/public/")
        assert status == expected.status_code == 200
        assert home_links(page) == home_links(expected.content)
        assert (b"Registration is open" in page) is registration
        assert status_text.encode() in page
        assert b"Native Tournament" in page
        assert b"Outside the main room" in page
        assert b"Tournament guide" in page
        assert b"Example Director" in page and b"Another Director" in page
        return page

    with rust_server() as port:
        assert request(port, "/")[1]["Location"] == "/public/"
        assert request(port, "/favicon.ico")[1]["Content-Type"] == "image/x-icon"
        first_page = compare(port, registration=True, status_text="Register new team")
        assert b"View debate motions" not in first_page
        school = School.objects.create(name="Examples")
        teams = [
            Team.objects.create(name=f"Example {i}", seed=Team.UNSEEDED, school=school)
            for i in range(2)
        ]
        Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1])
        TabSettings.set("cur_round", 2)
        compare(port, registration=False, status_text="Pairings in Progress")
        TabSettings.set("pairing_released", 1)
        compare(port, registration=False, status_text="Round 1 · Pairing released")

        Outround.objects.create(num_teams=8, gov_team=teams[0], opp_team=teams[1])
        Outround.objects.create(num_teams=4, gov_team=teams[0], opp_team=teams[1])
        TabSettings.set("var_teams_visible", 8)
        page = compare(port, registration=False, status_text="[V] Ro4 pairing in progress")
        assert home_links(page)[0] == "/public/outrounds/0/"
        assert "/public/tab-cards/" in home_links(page)


@pytest.mark.django_db(transaction=True)
def test_native_pairings_release_previous_rounds_and_missing_ballots():
    school = School.objects.create(name="Example School")
    teams = [
        Team.objects.create(name=name, seed=Team.UNSEEDED, school=school)
        for name in ("Released Government", "Released Opposition", "A Bye", "Unpaired")
    ]
    debater = Debater.objects.create(name="Name Visibility Example", novice_status=Debater.VARSITY)
    teams[0].debaters.add(debater)
    chair = Judge.objects.create(name="Assigned Chair", rank=8)
    wing = Judge.objects.create(name="Assigned Wing", rank=4)
    Judge.objects.create(name="Unassigned Judge", rank=9)
    room = Room.objects.create(name="Released Room", rank=1)
    Round.objects.create(round_number=1, gov_team=teams[1], opp_team=teams[0])
    round_obj = Round.objects.create(
        round_number=2, gov_team=teams[0], opp_team=teams[1], room=room,
    )
    round_obj.judges.add(chair, wing)
    Round.objects.filter(pk=round_obj.pk).update(chair=chair)
    Bye.objects.create(round_number=2, bye_team=teams[2])
    TabSettings.set("cur_round", 3)
    TabSettings.set("tot_rounds", 5)
    paths = ("/public/pairings/", "/public/pairings/1/", "/public/missing-ballots/")
    client = Client()

    with rust_server() as port:
        hidden, previous, missing = paths
        assert b"does not yet have a valid pairing" in request(port, hidden)[2]
        assert b"Assigned Chair" not in request(port, hidden)[2]
        assert b"Released Government" in request(port, previous)[2]
        assert request(port, "/public/pairings/2/")[0] == 404
        assert request(port, missing)[1]["Location"] == "/public/access-error/"

        TabSettings.set("pairing_released", 1)
        TabSettings.set("debaters_public", 0)
        for path in paths:
            assert request(port, path)[0] == client.get(path).status_code == 200
        page = request(port, hidden)[2]
        assert b"<b>Assigned Chair</b>" in page
        assert b"Assigned Wing" in page and b"Released Room" in page
        assert b"Unassigned Judge" not in page
        assert b"A Bye" in page and b"Unpaired" in page
        assert b"Name Visibility Example" not in page
        assert b"Assigned Chair" in request(port, missing)[2]
        Round.objects.filter(pk=round_obj.pk).update(victor=Round.GOV)
        assert b"All ballots entered!" in request(port, missing)[2]


def legacy_tab_session(username):
    user = get_user_model().objects.create_superuser(username=username, password="synthetic")
    client = Client()
    client.force_login(user)
    return client.cookies["sessionid"].value


def round_signature(number):
    return list(Round.objects.filter(round_number=number).order_by("id").values_list(
        "gov_team_id", "opp_team_id", "pullup",
    )), list(Bye.objects.filter(round_number=number).order_by("id").values_list(
        "bye_team_id", flat=True,
    )), list(NoShow.objects.filter(round_number=number).order_by("id").values_list(
        "no_show_team_id", flat=True,
    ))


@pytest.mark.django_db(transaction=True)
def test_native_pair_assign_publish_and_repair_are_atomic(tmp_path, monkeypatch):
    # Old cache expiry jitter draws from the same generator as side assignment.
    monkeypatch.setattr(cache_logic, "random", random.Random(1234))
    tab = legacy_tab_session("pairing-staff")
    board = legacy_tab_session("pairing-board")
    board_user = get_user_model().objects.get(username="pairing-board")
    board_user.groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 1)
    TabSettings.set("tot_rounds", 2)
    TabSettings.set("enable_room_seeding", 1)
    school = School.objects.create(name="Pairing School")
    teams = [
        Team.objects.create(name=f"Pairing Team {i}", school=school, seed=(i % 4),
                            checked_in=i < 5)
        for i in range(6)
    ]
    for index, team in enumerate(teams):
        team.debaters.add(*[
            Debater.objects.create(name=f"Pairing Speaker {index} {speaker}", novice_status=Debater.VARSITY)
            for speaker in range(2)
        ])
    judges = [Judge.objects.create(name=f"Pairing Judge {i}", rank=i + 2) for i in range(2)]
    rooms = [Room.objects.create(name=f"Pairing Room {i}", rank=Decimal(5 - i)) for i in range(2)]
    pair_page = "/pairing/pair_round/"
    repair_page = "/pairing/re_pair_round/"
    room_page = "/pairing/assign_rooms_to_pairing/"
    release_page = "/pairing/release/"
    status_page = "/pairings/status/"
    backup_dir = tmp_path / "backups"
    with rust_server(overrides={"BACKUP_STORAGE": "", "BACKUP_PREFIX": str(backup_dir)}) as port:
        status, headers, body = request(port, pair_page, cookie=tab)
        assert status == 200
        assert b"0 of 2" in body and b"disabled" in body
        csrf = load_cookies(headers)["csrftoken"].value

        def post(path, *, session=tab, round_number=1, token=csrf):
            return request(port, path, method="POST",
                           extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": token, "round": round_number})

        assert post(pair_page, token="invalid")[0] == 403
        assert post(pair_page)[0] == 409
        assert post(pair_page, session=board)[0] == 403
        assert not backup_dir.exists()
        for number in (1, 2):
            for judge, room in zip(judges, rooms):
                CheckIn.objects.create(judge=judge, round_number=number)
                RoomCheckIn.objects.create(room=room, round_number=number)

        with transaction.atomic():
            tab_logic.pair_round()
            legacy_round = round_signature(1)
            transaction.set_rollback(True)

        with rust_server(overrides={
            "BACKUP_STORAGE": "", "BACKUP_PREFIX": str(backup_dir),
            "MITTAB_MYSQLDUMP": str(tmp_path / "missing-dump-tool"),
        }) as broken_port:
            status, _, body = request(
                broken_port, pair_page, method="POST",
                extra_headers={"Cookie": f"sessionid={tab}; csrftoken={csrf}"},
                form={"csrfmiddlewaretoken": csrf, "round": 1},
            )
            assert status == 409 and b"pairings are unchanged" in body
            assert round_signature(1) == ([], [], [])

        assert post(pair_page)[1]["Location"] == status_page
        assert round_signature(1) == legacy_round
        assert len(legacy_round[0]) == 2 and len(legacy_round[1]) == 1
        assert legacy_round[2] == [teams[-1].id]
        assert post(pair_page)[0] == 409
        assert post(pair_page, round_number=2)[0] == 409
        assert round_signature(2) == ([], [], [])
        assert not b"Pairing Room 0" in request(port, status_page, cookie=tab)[2]

        first, second = list(Round.objects.filter(round_number=1).order_by("id"))
        tag = RoomTag.objects.create(tag="Accessible Room", priority=Decimal("4.75"))
        judges[0].required_room_tags.add(tag)
        second.judges.add(judges[0])
        rooms[1].tags.add(tag)
        assert post(room_page)[1]["Location"] == status_page
        first.refresh_from_db()
        second.refresh_from_db()
        assert (first.room_id, second.room_id) == (rooms[0].id, rooms[1].id)
        assert b"Pairing Room 1" in request(port, status_page, cookie=tab)[2]
        RoomCheckIn.objects.filter(room=rooms[1], round_number=1).delete()
        assert post(room_page)[0] == 409
        second.refresh_from_db()
        assert second.room_id == rooms[1].id
        RoomCheckIn.objects.create(room=rooms[1], round_number=1)

        assert post(release_page)[0] == 302
        assert b"Pairing Room 1" in request(port, "/public/pairings/")[2]
        assert post(release_page)[0] == 302
        assert b"Pairing Room 1" not in request(port, "/public/pairings/")[2]

        RoundStats.objects.create(round=first, debater=first.gov_team.debaters.first(),
                                  speaks=26, ranks=1, debater_role=None)
        assert post(repair_page)[0] == 302
        assert round_signature(1) == legacy_round
        assert not Round.objects.filter(pk__in=[first.id, second.id]).exists()
        assert not RoundStats.objects.exists()
        deleted = AuditEvent.objects.filter(object_id=first.id, event_type="edit")
        snapshots = [event.changes["public"] for event in deleted]
        assert any(item["after"] is None and item["before"]["scores"] for item in snapshots)
        assert all("rank" not in judge for item in snapshots
                   for snapshot in (item["before"], item["after"]) if snapshot
                   for judge in snapshot["judges"])

        for battle in Round.objects.order_by("id"):
            Round.objects.filter(pk=battle.pk).update(victor=Round.GOV)
            for team in (battle.gov_team, battle.opp_team):
                for index, debater in enumerate(team.debaters.all()):
                    RoundStats.objects.create(round=battle, debater=debater,
                                              speaks=Decimal("26.25") + index,
                                              ranks=index + 1)
        caches["default"].clear()
        caches["filesystem"].clear()
        with transaction.atomic():
            assert TabSettings.get("cur_round") == 2
            tab_logic.pair_round()
            expected_second = round_signature(2)
            transaction.set_rollback(True)
        assert post(pair_page, round_number=2)[0] == 302
        assert round_signature(2) == expected_second
        Round.objects.filter(round_number=2).update(victor=Round.GOV)
        for path in (pair_page, repair_page, room_page, release_page, status_page):
            assert request(port, path, cookie=board)[1]["Location"] == "/403/"
            assert post(path, session=board, round_number=2)[1]["Location"] == "/403/"

    backups = list(backup_dir.glob("*.dump.sql"))
    assert len(backups) == 4
    assert not list(backup_dir.glob(".backup-*"))
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in backups)
    for path in backups:
        dump = path.read_text()
        assert "CREATE TABLE `tab_team`" in dump
        assert "Pairing Team 0" in dump


@pytest.mark.django_db(transaction=True)
def test_simultaneous_all_bye_pairing_cannot_advance_two_rounds(tmp_path):
    tab = legacy_tab_session("pairing-race")
    TabSettings.set("cur_round", 1)
    TabSettings.set("min_byes", 2)
    school = School.objects.create(name="All Bye School")
    for i in range(2):
        Team.objects.create(name=f"All Bye Team {i}", seed=Team.UNSEEDED,
                            school=school, checked_in=True)
    pair_page = "/pairing/pair_round/"
    with rust_server(overrides={"BACKUP_STORAGE": "", "BACKUP_PREFIX": str(tmp_path)}) as port:
        status, headers, _ = request(port, pair_page, cookie=tab)
        assert status == 200
        csrf = load_cookies(headers)["csrftoken"].value

        def advance(_):
            return request(
                port, pair_page, method="POST",
                extra_headers={"Cookie": f"sessionid={tab}; csrftoken={csrf}"},
                form={"csrfmiddlewaretoken": csrf, "round": 1},
            )[0]

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(advance, range(2))) == [302, 409]
        assert Bye.objects.filter(round_number=1).count() == 2
        assert Bye.objects.filter(round_number=2).count() == 0
    assert len(list(tmp_path.glob("*.dump.sql"))) == 1


@pytest.mark.django_db(transaction=True)
def test_native_judge_assignment_matches_django_for_all_prelim_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_logic, "random", random.Random(1234))
    tab = legacy_tab_session("assignment-staff")
    TabSettings.set("cur_round", 3)
    schools = [School.objects.create(name=f"Assignment School {i}") for i in range(4)]
    teams = [
        Team.objects.create(name=f"Assignment Team {i}", school=schools[i // 2],
                            hybrid_school=schools[-1] if i == 2 else None,
                            seed=Team.UNSEEDED, checked_in=True)
        for i in range(8)
    ]
    for index, team in enumerate(teams):
        team.debaters.add(*[
            Debater.objects.create(name=f"Assignment Speaker {index} {speaker}",
                                   novice_status=Debater.VARSITY)
            for speaker in range(2)
        ])
    judges = [
        Judge.objects.create(name=f"Assignment Judge {i:02}", rank=Decimal(8 - i // 2),
                             wing_only=i in (1, 3, 10), request_wing=i in (2, 4), no_wings=i == 7)
        for i in range(11)
    ]
    for judge in judges:
        CheckIn.objects.create(judge=judge, round_number=2)
    judges[1].schools.add(schools[0])
    judges[2].schools.add(schools[-1])
    Scratch.objects.create(team=teams[0], judge=judges[0], scratch_type=Scratch.TAB_SCRATCH)
    Scratch.objects.create(team=teams[4], judge=judges[3], scratch_type=Scratch.TEAM_SCRATCH)
    for index in range(4):
        gov, opp = teams[index * 2:index * 2 + 2]
        debate = Round.objects.create(round_number=1, gov_team=gov, opp_team=opp,
                                      victor=Round.GOV if index % 2 == 0 else Round.OPP)
        debate.judges.add(judges[index * 2], judges[index * 2 + 1])
        Round.objects.filter(id=debate.id).update(chair=judges[index * 2])
        for member in (gov, opp):
            for i, speaker in enumerate(member.debaters.all()):
                RoundStats.objects.create(round=debate, debater=speaker,
                                          speaks=Decimal(26 + ((index + i) % 3)), ranks=i + 1)
        Round.objects.create(round_number=2, gov_team=teams[index],
                             opp_team=teams[(index + 1) % 4 + 4])
    path = "/pairing/assign_judges/"

    def signature():
        return [(debate.id, debate.chair_id, tuple(sorted(j.id for j in debate.judges.all())))
                for debate in Round.objects.filter(round_number=2).order_by("id")]

    with rust_server(overrides={"BACKUP_STORAGE": "", "BACKUP_PREFIX": str(tmp_path)}) as port:
        status, headers, _ = request(port, "/pairings/status/", cookie=tab)
        assert status == 200
        csrf = load_cookies(headers)["csrftoken"].value

        def assign():
            return request(
                port, path, method="POST",
                extra_headers={"Cookie": f"sessionid={tab}; csrftoken={csrf}"},
                form={"csrfmiddlewaretoken": csrf, "round": 2},
            )

        for wing_mode in (0, 1, 2):
            for pairing_mode in (0, 1):
                for allow_rejudges in (0, 1):
                    TabSettings.set("wing_pairing_mode", wing_mode)
                    TabSettings.set("judge_pairing_mode", pairing_mode)
                    TabSettings.set("allow_rejudges", allow_rejudges)
                    TabSettings.set("inround_round_priority", wing_mode % 2)
                    TabSettings.set("pair_wings", 1)
                    caches["default"].clear()
                    caches["filesystem"].clear()
                    with transaction.atomic():
                        assign_judges.add_judges()
                        expected = signature()
                        transaction.set_rollback(True)
                    assert assign()[0] == 302
                    assert signature() == expected, (wing_mode, pairing_mode, allow_rejudges)

        actual = signature()
        assert AuditEvent.objects.filter(event_type="judges_paired").count() == 12 * 4
        assert b"<b>Assignment Judge" in request(port, "/pairings/status/", cookie=tab)[2]
        Judge.objects.filter(pk__in=[judge.id for judge in judges]).update(wing_only=True)
        status, _, body = assign()
        assert status == 409 and b"eligible chair" in body
        assert signature() == actual
        assert AuditEvent.objects.filter(event_type="judges_paired").count() == 12 * 4


@pytest.mark.django_db(transaction=True)
def test_native_single_and_panel_ballots_preserve_existing_results_on_invalid_input():
    from mittab.apps.tab.forms import ResultEntryForm, score_panel

    tab = legacy_tab_session("native-ballots")
    board = legacy_tab_session("ballots-board")
    get_user_model().objects.get(username="ballots-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    TabSettings.set("decimal_speaks", 3)
    TabSettings.set("written_rfd_first_round", 1)
    TabSettings.set("written_rfd_deadline", "2099-01-01T19:00")
    school = School.objects.create(name="Ballot School")
    teams = [Team.objects.create(name=f"Ballot Team {i}", school=school, seed=Team.UNSEEDED) for i in range(2)]
    speakers = [
        Debater.objects.create(name=f"Ballot Speaker {i}", novice_status=Debater.VARSITY)
        for i in range(4)
    ]
    teams[0].debaters.add(*speakers[:2])
    teams[1].debaters.add(*speakers[2:])
    debate = Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1])
    NoShow.objects.create(round_number=1, no_show_team=teams[0])
    single = f"/round/{debate.id}/result/"
    panel = f"{single}3/"
    roles = ("pm", "mg", "lo", "mo")

    def ballot(winner, values, ranks, prefix=""):
        fields = {f"{prefix}winner": winner, f"{prefix}round_instance": debate.id}
        for role, speaker, speaks, rank in zip(roles, speakers, values, ranks):
            fields.update({
                f"{prefix}{role}_debater": speaker.id,
                f"{prefix}{role}_speaks": speaks,
                f"{prefix}{role}_ranks": rank,
            })
        return fields

    first = ballot(Round.GOV, (30.23, 29, 28, 27), (1, 2, 3, 4))
    legacy = ResultEntryForm(first, round_instance=debate)
    assert legacy.is_valid(), legacy.errors

    with rust_server() as port:
        status, headers, body = request(port, single, cookie=tab)
        assert status == 200 and b"Ballot Speaker 3" in body and b"Reason for decision" in body
        csrf = load_cookies(headers)["csrftoken"].value

        def post(path, fields, *, token=csrf, session=tab):
            return request(
                port, path, method="POST", extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                form={"csrfmiddlewaretoken": token, **fields},
            )

        assert post(single, first, token="bad")[0] == 403
        assert post(single, first, session=board)[0] == 403
        for invalid in (
            {"winner": 2}, {"pm_speaks": "NaN"}, {"pm_speaks": "30.0001"}, {"pm_ranks": 2},
            {"pm_debater": speakers[3].id}, {"round_instance": debate.id + 1},
        ):
            assert post(single, {**first, **invalid})[0] == 422
            debate.refresh_from_db()
            assert debate.victor == Round.UNKNOWN and debate.result_entered_at is None
            assert not debate.roundstats_set.exists()

        assert post(single, {**first, "rfd": " Synthetic note "})[1]["Location"] == "/pairings/status/"
        debate.refresh_from_db()
        assert debate.victor == Round.GOV and debate.result_entered_at
        entered = debate.result_entered_at
        assert debate.rfd == "Synthetic note"
        assert not NoShow.objects.exists()
        scores = dict(debate.roundstats_set.values_list("debater_role", "speaks"))
        assert scores["pm"] == Decimal("30.2300")
        assert post(single, {"rfd": "Edited reason"})[1]["Location"] == single
        debate.refresh_from_db()
        assert debate.rfd == "Edited reason" and debate.result_entered_at == entered
        assert post(single, first, session=board)[1]["Location"] == "/403/"
        assert request(port, single, cookie=board)[1]["Location"] == "/403/"

        cases = [
            (Round.GOV, (30.23, 29, 28, 27), (1, 2, 3, 4)),
            (Round.OPP, (28, 27, 30.5, 29), (3, 4, 1, 2)),
            (Round.GOV, (31, 29, 27, 26), (1, 2, 3, 4)),
        ]
        grouped = {}
        fields = {}
        for i, (winner, speaks, ranks) in enumerate(cases, 1):
            fields.update(ballot(winner, speaks, ranks, f"{i}-"))
            grouped.setdefault(winner, []).append(list(zip(speakers, roles, speaks, ranks)))

        prior_ids = list(debate.roundstats_set.order_by("id").values_list("id", flat=True))
        assert post(panel, {**fields, "2-pm_debater": speakers[1].id})[0] == 422
        assert list(debate.roundstats_set.order_by("id").values_list("id", flat=True)) == prior_ids
        for discard_minority in (False, True):
            expected, winner = score_panel(grouped, discard_minority)
            data = {**fields, "discard_minority": "on"} if discard_minority else fields
            assert post(panel, data)[0] == 302
            debate.refresh_from_db()
            assert debate.victor == winner and debate.result_entered_at == entered
            stored = dict(
                (role, (debater, speaks, ranks)) for role, debater, speaks, ranks
                in debate.roundstats_set.values_list("debater_role", "debater_id", "speaks", "ranks")
            )
            assert stored == {
                role: (debater.id, Decimal(f"{speaks:.4f}"), ranks)
                for debater, role, speaks, ranks in expected
            }

        TabSettings.set("written_rfd_deadline", "2000-01-01T00:00")
        assert post(single, {"rfd": "Too late"})[0] == 422
        debate.refresh_from_db()
        assert debate.rfd == "Edited reason"
        history = list(AuditEvent.objects.values_list("changes", flat=True))
        assert {event["public"]["source"] for event in history} == {"ballot entry", "panel ballot entry"}
        assert "Synthetic note" not in json.dumps(history) and "Edited reason" not in json.dumps(history)


@pytest.mark.django_db(transaction=True)
def test_native_judge_portal_availability_current_previous_and_reason_edits():
    path = "/public/e-ballots/"
    chair_code = "Synthetic-Chair-a1"
    wing_code = "Synthetic-Wing-b2"
    other_code = "Synthetic-Other-c3"
    chair = Judge.objects.create(name="Portal Chair", rank=5, ballot_code=chair_code)
    wing = Judge.objects.create(name="Portal Wing", rank=4, ballot_code=wing_code)
    other = Judge.objects.create(name="Portal Other", rank=3, ballot_code=other_code)
    JudgeExpectedCheckIn.objects.create(judge=chair, round_number=1)
    JudgeExpectedCheckIn.objects.create(judge=other, round_number=2)
    board = legacy_tab_session("portal-board")
    get_user_model().objects.get(username="portal-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    TabSettings.set("decimal_speaks", 3)
    TabSettings.set("written_rfd_first_round", 1)
    TabSettings.set("written_rfd_deadline", "2099-01-01T19:00")
    TabSettings.set("open_speaks", 1)
    TabSettings.set("open_ranks", 2)
    school = School.objects.create(name="Portal School")
    teams = [Team.objects.create(name=f"Portal Team {i}", school=school, seed=Team.UNSEEDED) for i in range(2)]
    speakers = [Debater.objects.create(name=f"Portal Speaker {i}", novice_status=Debater.VARSITY) for i in range(4)]
    teams[0].debaters.add(*speakers[:2])
    teams[1].debaters.add(*speakers[2:])
    debate = Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1])
    debate.judges.add(chair, wing)
    Round.objects.filter(id=debate.id).update(chair=chair)
    chair_portal = f"{path}{chair_code}/"
    chair_current = f"{chair_portal}current/"
    chair_previous = f"{chair_portal}previous/"
    chair_submitted = f"{chair_portal}submitted/"
    chair_specific = f"{chair_submitted}{debate.id}/"
    wing_current = f"{path}{wing_code}/current/"
    wing_specific = f"{path}{wing_code}/submitted/{debate.id}/"
    other_specific = f"{path}{other_code}/submitted/{debate.id}/"

    with rust_server() as port:
        status, headers, page = request(port, path)
        assert status == 200 and b"Judge code" in page
        assert headers["Referrer-Policy"] == "same-origin"
        assert headers["Cache-Control"] == "private, no-store"
        csrf = load_cookies(headers)["csrftoken"].value

        def post(url, fields, *, token=csrf, session=None, origin=None):
            cookie = f"csrftoken={csrf}" + (f"; sessionid={session}" if session else "")
            extra = {"Cookie": cookie}
            if origin:
                extra["Origin"] = origin
            return request(port, url, method="POST", extra_headers=extra,
                           form={"csrfmiddlewaretoken": token, **fields})

        assert post(path, {"ballot_code": chair_code})[1]["Location"] == chair_portal
        assert post(path, {"ballot_code": "unexpected/path"})[0] == 422
        assert request(port, chair_portal, cookie=board)[0] == 403
        page = request(port, chair_portal)[2]
        assert b"Portal Chair" in page and b'for="availability_round_1"' in page
        assert post(chair_portal, {"availability_outround": "on"}, token="bad")[0] == 403
        assert set(chair.expected_checkins.values_list("round_number", flat=True)) == {1}
        assert post(chair_portal, {"availability_outround": "on", "availability_round_99": "on"})[0] == 302
        assert set(chair.expected_checkins.values_list("round_number", flat=True)) == {0}
        assert set(other.expected_checkins.values_list("round_number", flat=True)) == {2}

        data = {"winner": 1, "ballot_code": chair_code, "round_instance": debate.id, "rfd": " First reason "}
        for i, (role, speaker) in enumerate(zip(("pm", "mg", "lo", "mo"), speakers)):
            data.update({f"{role}_debater": speaker.id, f"{role}_speaks": 31 - i, f"{role}_ranks": i + 1})
        assert request(port, chair_current)[1]["Location"] == chair_previous
        assert post(chair_current, data)[1]["Location"] == chair_previous
        assert not debate.roundstats_set.exists()
        TabSettings.set("pairing_released", 1)
        status, _, page = request(port, chair_current)
        assert status == 200 and b"Portal Speaker 0" in page
        assert b"Government by forfeit" not in page
        assert request(port, wing_current)[0] == 403
        assert post(wing_current, {**data, "ballot_code": wing_code})[0] == 403
        assert post(chair_current, data, origin="https://example.invalid")[0] == 403
        for invalid in (
            {"round_instance": debate.id + 1}, {"winner": 3}, {"pm_speaks": 36}, {"ballot_code": wing_code},
        ):
            assert post(chair_current, {**data, **invalid})[0] == 422
            assert not debate.roundstats_set.exists()

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: post(chair_current, data), range(2)))
        assert all(status == 302 and response["Location"] == chair_submitted for status, response, _ in responses)
        debate.refresh_from_db()
        assert debate.victor == Round.GOV and debate.rfd == "First reason"
        assert debate.roundstats_set.count() == 4
        assert request(port, chair_current)[1]["Location"] == chair_submitted
        status, _, page = request(port, chair_submitted)
        assert status == 200 and b"First reason" in page
        assert b"Disclose speaker points" in page and b"Ranks are closed" in page
        assert request(port, wing_specific)[0] == 200
        assert request(port, other_specific)[1]["Location"] == f"{path}{other_code}/previous/"
        assert post(wing_specific, {"rfd": "Forbidden change"})[0] == 403
        assert post(chair_specific, {"rfd": "Updated reason"})[0] == 302
        debate.refresh_from_db()
        assert debate.rfd == "Updated reason"
        assert b"Written" in request(port, chair_previous)[2]

        for protected in (path, chair_portal, chair_current, chair_previous, chair_specific, chair_submitted):
            assert request(port, protected, cookie=board)[1]["Location"] == "/403/"
            assert request(port, protected, cookie=board, method="HEAD")[1]["Location"] == "/403/"
        assert post(chair_specific, {"rfd": "Board write"}, session=board)[1]["Location"] == "/403/"
        assert post(chair_portal, {"availability_round_1": "on"}, session=board)[1]["Location"] == "/403/"
        assert set(chair.expected_checkins.values_list("round_number", flat=True)) == {0}
        debate.refresh_from_db()
        assert debate.rfd == "Updated reason"

        TabSettings.set("written_rfd_deadline", "2000-01-01T00:00")
        assert post(chair_specific, {"rfd": "Late edit"})[0] == 403
        debate.refresh_from_db()
        assert debate.rfd == "Updated reason"


@pytest.mark.django_db(transaction=True)
def test_native_board_bracket_operations_allow_only_team_room_and_result_fields():
    user = get_user_model().objects.create_superuser(username="board-brackets", password="synthetic")
    user.groups.add(Group.objects.create(name="APDA Board"))
    client = Client()
    client.force_login(user)
    board = client.cookies["sessionid"].value
    tab = legacy_tab_session("board-brackets-tab")
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    TabSettings.set("var_teams_to_break", 4)
    TabSettings.set("nov_teams_to_break", 0)
    school = School.objects.create(name="Board Bracket School")
    teams = [
        Team.objects.create(name=f"Board Bracket Team {i}", school=school, seed=Team.UNSEEDED,
                            checked_in=True, tiebreaker=i + 1)
        for i in range(4)
    ]
    judges = [
        Judge.objects.create(name=f"Board Bracket Judge {i}", rank=i + 1,
                             ballot_code=f"Synthetic-Board-{i}")
        for i in range(2)
    ]
    debate = Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1])
    debate.judges.add(*judges)
    Round.objects.filter(id=debate.id).update(chair=judges[0], rfd="private reason fixture")
    rooms = [Room.objects.create(name=f"Board Bracket Room {i}", rank=10 - i) for i in range(3)]
    home = "/apda-board/"
    inrounds = f"{home}inrounds/"
    outrounds = f"{home}outrounds/"
    judge_page = f"{home}judges/"
    pair = f"{outrounds}pair/"
    paths = (home, inrounds, outrounds, judge_page)

    with rust_server() as port:
        for path in paths:
            assert request(port, path, cookie=board)[0] == 403
        assert request(port, outrounds, cookie=tab)[0] == 403
        Round.objects.filter(id=debate.id).update(victor=Round.GOV)
        status, headers, body = request(port, outrounds, cookie=board)
        assert status == 200 and b"Pair Next Varsity Round" in body
        csrf = load_cookies(headers)["csrftoken"].value
        jar = {"Cookie": f"sessionid={board}; csrftoken={csrf}"}

        def post(path, data, *, token=csrf):
            return request(port, path, method="POST", extra_headers=jar,
                           form={"csrfmiddlewaretoken": token, **data})

        assert post(pair, {"division": 0}, token="invalid")[0] == 403
        assert post(pair, {"division": 0, "judge": judges[0].id})[0] == 422
        assert post(pair, {"division": 0})[0] == 409
        assert not Outround.objects.exists()
        assert not BreakingTeam.objects.exists()
        for room in rooms:
            RoomCheckIn.objects.create(room=room, round_number=0)
        assert post(pair, {"division": 0})[1]["Location"] == outrounds
        assert post(pair, {"division": 0})[0] == 409
        first, second = list(Outround.objects.filter(num_teams=4).order_by("id"))
        assert (first.room_id, second.room_id) == (rooms[0].id, rooms[1].id)
        assert first.chair_id is None and not first.judges.exists()
        for snapshot in (first, second):
            snapshot.judges.add(*judges)
        Outround.objects.filter(id__in=(first.id, second.id)).update(chair=judges[0])
        AuditEvent.record(
            debate, AuditEvent.MANUAL_CHAIR_CHANGE, user,
            changes={"new_chair": judges[0].name, "rank": "protected rank fixture"},
            note="protected note fixture",
        )
        AuditEvent.record(
            first, AuditEvent.EDIT, user,
            changes={"public": {"source": "staff room correction", "before": {"room": None}, "after": {
                "room": {"id": rooms[0].id, "name": rooms[0].name, "note": "protected room fixture"},
                "scratches": ["protected scratch fixture"],
            }}},
        )
        before = {path: request(port, path, extra_headers=jar)[2] for path in paths}
        assert b"(chair)" in before[inrounds] and b"New Chair" in before[inrounds]
        assert b"Manual edits" in before[outrounds]
        assert b"New Chair" in request(port, f"{inrounds}?manual=1", extra_headers=jar)[2]
        for body in before.values():
            assert b"protected" not in body and b"private reason fixture" not in body
            assert b"Synthetic-Board" not in body

        Scratch.objects.create(team=teams[0], judge=judges[0], scratch_type=Scratch.TAB_SCRATCH)
        Judge.objects.filter(id=judges[0].id).update(rank=Decimal("9.57"), wing_only=True,
                                                   request_wing=True, no_wings=True)
        assert {path: request(port, path, extra_headers=jar)[2] for path in paths} == before
        assert request(port, f"{home}inrounds/1/", method="HEAD", extra_headers=jar)[0] == 200
        assert post(inrounds, {"action": "result", "victor": 1})[1]["Location"] == "/403/"
        assert request(port, pair, extra_headers=jar)[1]["Location"] == "/403/"
        assert post(judge_page, {"judge": judges[0].id, "is_dino": "on", "rank": 3})[2] == b"Invalid form submission."
        assert post(judge_page, {"judge": judges[0].id, "is_dino": "on"})[0] == 302
        judges[0].refresh_from_db()
        assert judges[0].is_dino and judges[0].rank == Decimal("9.57") and judges[0].wing_only
        assert judges[0].ballot_code == "Synthetic-Board-0"
        assert list(Scratch.objects.values_list("team_id", "judge_id")) == [(teams[0].id, judges[0].id)]

        def token_for(round_id):
            page = request(port, outrounds, extra_headers=jar)[2].decode()
            found = re.search(
                rf'action="{home}outround/{round_id}/"[^>]*>\s*<input[^>]*>\s*<input type="hidden" name="revision" value="([^"]+)"',
                page,
            )
            assert found is not None
            return found[1]

        def correction(round_id, data, revision=None):
            return post(f"{home}outround/{round_id}/", {"revision": revision or token_for(round_id), **data})

        revision = token_for(first.id)
        assert correction(first.id, {"action": "room", "room": rooms[1].id}, revision)[0] == 409
        assert correction(first.id, {"action": "judge", "judge": judges[1].id}, revision)[0] == 422
        assert correction(first.id, {"action": "choice", "choice": 1}, revision)[0] == 302
        assert correction(first.id, {"action": "sides"}, revision)[0] == 409
        first.refresh_from_db()
        old = first.gov_team_id, first.opp_team_id
        assert correction(first.id, {"action": "sides"})[0] == 302
        first.refresh_from_db()
        assert (first.gov_team_id, first.opp_team_id) == old[::-1] and first.choice == 2
        second.refresh_from_db()
        replacement = second.gov_team_id
        original = first.gov_team_id
        assert correction(first.id, {"action": "team", "position": "gov", "team": replacement})[0] == 302
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.gov_team_id == replacement and second.gov_team_id == original
        assert correction(first.id, {"action": "team", "position": "gov", "team": original})[0] == 302
        assert correction(first.id, {"action": "room", "room": rooms[2].id})[0] == 302
        for matchup in (first, second):
            assert correction(matchup.id, {"action": "result", "victor": Outround.GOV})[0] == 302
        assert post(pair, {"division": 0})[0] == 302
        final = Outround.objects.get(num_teams=2)
        assert {final.gov_team_id, final.opp_team_id} == {replacement, original}
        assert final.chair_id is None and not final.judges.exists()
        for matchup in (first, second):
            matchup.refresh_from_db()
            assert matchup.chair_id == judges[0].id
            assert set(matchup.judges.values_list("id", flat=True)) == {j.id for j in judges}
        debate.refresh_from_db()
        assert debate.victor == Round.GOV and debate.rfd == "private reason fixture"
        assert debate.chair_id == judges[0].id
        assert Scratch.objects.count() == 1
        for changes in AuditEvent.objects.filter(user=user).values_list("changes", flat=True):
            snapshot = changes.get("public", {})
            if snapshot.get("source") in {"board pairing", "board team swap", "board correction"}:
                for frame in (snapshot.get("before"), snapshot.get("after")):
                    if frame:
                        assert set(frame) == {"gov_team", "opp_team", "room", "judges", "chair",
                                              "victor", "num_teams", "division", "choice", "sidelock"}
                        assert all(set(judge) == {"id", "name"} for judge in frame["judges"])


@contextmanager
def mock_school_directory():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class DirectoryHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/api/schools/all/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"schools": [
                {"id": 17, "name": "Remote Institute"},
                {"apda_id": 20, "name": "Another Remote School"},
            ]}).encode())

        def log_message(self, _format, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), DirectoryHandler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/api/schools/all/"
        finally:
            server.shutdown()
            worker.join(timeout=5)


@pytest.mark.django_db(transaction=True)
def test_native_wizard_and_checklist_unlock_staff_on_valid_basics():
    from mittab.apps.tab.models import TournamentStaffRole, UserTournamentSetupPreference

    tab = legacy_tab_session("setup-staff")
    board = legacy_tab_session("setup-board")
    get_user_model().objects.get(username="setup-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("tournament_name", "New Tournament")
    TabSettings.set("start_tournament_wizard_completed", 0)
    TabSettings.set("cur_round", 1)
    TabSettings.set("tot_rounds", 5)
    school = School.objects.create(name="Local Host")
    InfoText.objects.create(title="GA", text="TBD", is_active=True)
    basics = "/setup/start-tournament/"
    todo = "/setup/tournament-todo/"
    toggle = f"{todo}toggle/"
    ready = {
        "tournament_name": "Rust Invitational",
        "host_school": "apda:17",
        "tournament_type": "12",
        "ga_room": "Main Library 102",
        "facebook_url": "https://www.facebook.com/events/1",
        "sanctioned": "on",
        "tournament_director": "Taylor Director",
        "tab_director": "Sam Director",
        "equity_officers": "Alex Officer\nMorgan Officer",
        "tournament_observer": "Jordan Observer",
        "open_speaks": "2",
        "open_ranks": "1",
        "custom_staff-TOTAL_FORMS": "1",
        "custom_staff-0-title": "Room Coordinator",
        "custom_staff-0-names": "Robin Coordinator",
    }
    with mock_school_directory() as endpoint, rust_server(overrides={"MITTAB_SCHOOL_DIRECTORY_URL": endpoint}) as port:
        assert request(port, "/", cookie=tab)[1]["Location"] == basics
        assert request(port, todo, cookie=tab)[1]["Location"] == basics
        assert request(port, basics, cookie=board)[0] == 403
        status, headers, page = request(port, basics, cookie=tab)
        assert status == 200 and b"Start Tournament" in page
        assert b"Remote Institute" in page and b"Local Host" in page
        assert b'value="New Tournament"' not in page and b'value="TBD"' not in page
        csrf = load_cookies(headers)["csrftoken"].value
        jar = {"Cookie": f"sessionid={tab}; csrftoken={csrf}"}

        def post(path, form, token=csrf):
            return request(port, path, method="POST", extra_headers=jar,
                           form={"csrfmiddlewaretoken": token, **form})

        assert post(basics, ready, token="invalid")[0] == 403
        for invalid in ({"host_school": "apda:999"},
                        {"custom_staff-0-names": ""},
                        {"open_speaks": "3"},
                        {"facebook_url": "javascript:alert(1)"}):
            assert post(basics, {**ready, **invalid})[0] == 422
            assert TabSettings.get("start_tournament_wizard_completed", use_cache=False) == 0
            assert not TournamentStaffRole.objects.exists()

        assert post(basics, ready)[1]["Location"] == todo
        assert TabSettings.get("start_tournament_wizard_completed", use_cache=False) == 1
        assert TabSettings.get("host_school_name", use_cache=False) == "Remote Institute"
        assert TabSettings.get("tournament_type", use_cache=False) == 12
        assert TabSettings.get("open_ranks", use_cache=False) == 1
        tournament_date = TabSettings.get("tournament_date", use_cache=False)
        assert tournament_date
        assert InfoText.objects.get(title="GA").text == "Main Library 102"
        assert InfoLink.objects.get(title="Facebook").url == ready["facebook_url"]
        assert TournamentStaffRole.objects.get().title == "Room Coordinator"
        assert b"Robin Coordinator" in request(port, "/public/")[2]
        assert request(port, "/", cookie=tab)[1]["Location"] == "/pairings/status/"

        status, _, page = request(port, todo, extra_headers=jar)
        assert status == 200 and b"Configure Core Settings" in page
        assert b"Day of Tournament" in page and b"Speaker Results Auto-Published" in page
        assert re.search(rb'id="step_set_tournament_name"[^>]* checked[^>]* disabled', page)
        assert post(toggle, {"field": "step_unrecognized", "checked": "true"})[0] == 400
        activity = post(toggle, {"field": "step_add_tab_user_accounts", "checked": "true"})
        assert activity[0] == 200 and json.loads(activity[2])["section_completed"] == 2
        refusal = post(toggle, {"field": "step_set_tournament_name", "checked": "false"})
        assert json.loads(refusal[2])["checked"] is True
        assert post(todo, {"hide_tournament_todo": "on"})[0] == 302
        assert UserTournamentSetupPreference.objects.get().hide_tournament_todo
        assert TabSettings.get("todo_add_tab_user_accounts", use_cache=False) == 0
        assert TabSettings.get("todo_set_tournament_name", use_cache=False) == 1

        assert post(basics, {**ready, "host_school": f"pk:{school.id}",
                             "custom_staff-0-DELETE": "on"})[1]["Location"] == basics
        assert TabSettings.get("host_school_name", use_cache=False) == "Local Host"
        assert TabSettings.get("tournament_date", use_cache=False) == tournament_date
        assert not TournamentStaffRole.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_native_settings_write_only_declared_fields_and_honor_discretionary_scratch_lock():
    from mittab.apps.tab.views.views import get_settings_from_yaml

    tab = legacy_tab_session("settings-staff")
    TabSettings.set("cur_round", 1)
    TabSettings.set("tot_rounds", 5)
    TabSettings.set("judges_public", 1)
    registration, _ = RegistrationConfig.objects.update_or_create(
        id=1, defaults={"disc_scratches_open": True},
    )
    fields, _, categories = get_settings_from_yaml()
    path = "/settings_form"
    data = {}
    for setting in fields:
        value = setting["value"]
        if setting.get("type") == "choice" and value in ("", None):
            value = setting["choices"][0][0]
        if setting.get("type") == "boolean":
            if value:
                data[f"setting_{setting['name']}"] = "on"
        else:
            data[f"setting_{setting['name']}"] = value
    data.pop("setting_judges_public", None)
    data["setting_tot_rounds"] = "7"
    data["setting_written_rfd_deadline"] = "2099-11-10T20:15"

    with rust_server() as port:
        status, headers, page = request(port, path, cookie=tab)
        assert status == 200
        assert b"Tab Settings" in page and b"Speaker Points" in page
        assert re.search(rb'id="setting_judges_public"[^>]* disabled', page)
        csrf = load_cookies(headers)["csrftoken"].value
        jar = {"Cookie": f"sessionid={tab}; csrftoken={csrf}"}

        def post(path, form):
            return request(port, path, method="POST", extra_headers=jar,
                           form={"csrfmiddlewaretoken": csrf, **form})

        assert post(path, {**data, "setting_min_byes": "-1"})[0] == 422
        assert TabSettings.get("tot_rounds", use_cache=False) == 5
        assert post(path, {**data, "setting_cur_round": "10"})[1]["Location"] == path
        assert TabSettings.get("tot_rounds", use_cache=False) == 7
        assert TabSettings.get("cur_round", use_cache=False) == 1
        assert TabSettings.get("judges_public", use_cache=False) == 1
        assert TabSettings.get("written_rfd_deadline", use_cache=False) == "2099-11-10 20:15"
        registration.disc_scratches_open = False
        registration.save(update_fields=["disc_scratches_open"])
        assert post(path, data)[0] == 302
        assert TabSettings.get("judges_public", use_cache=False) == 0
        viewed = f"{path}/viewed/"
        assert post(viewed, {"category": "invalid"})[0] == 400
        for category in categories:
            assert post(viewed, {"category": category["id"]})[0] == 204
        page = request(port, "/setup/tournament-todo/", extra_headers=jar)[2]
        assert re.search(rb'id="step_configure_settings"[^>]* checked[^>]* disabled', page)


@pytest.mark.django_db(transaction=True)
def test_native_batch_checkin_and_history_restore_keep_durable_cells_and_role_boundaries():
    from mittab.apps.tab.models import BulkCheckInChange

    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 2)
    TabSettings.set("teams_public", 1)
    TabSettings.set("judges_public", 1)
    school = School.objects.create(name="Check-In School")
    teams = [
        Team.objects.create(name=f"Check-In Team {i}", school=school, seed=Team.UNSEEDED, checked_in=False)
        for i in range(2)
    ]
    judges = [Judge.objects.create(name=f"Check-In Judge {i}", rank=4) for i in range(2)]
    room = Room.objects.create(name="Check-In Room", rank=3)
    Round.objects.create(round_number=2, gov_team=teams[0], opp_team=teams[1], victor=Round.GOV)
    helper = get_user_model().objects.create_user(username="native-checkin", password="synthetic", is_staff=True)
    helper.groups.add(Group.objects.create(name="MIT-TAB: Check-in helper"))
    helper_client = Client()
    helper_client.force_login(helper)
    helper_cookie = helper_client.cookies["sessionid"].value
    board = legacy_tab_session("board-checkin")
    get_user_model().objects.get(username="board-checkin").groups.add(Group.objects.create(name="APDA Board"))
    page_url, update_url = "/batch_checkin/", "/bulk_check_in/"

    def post(port, session, csrf, kind="team", action="check_in", ids=(), rounds=(), **more):
        form = [
            ("csrfmiddlewaretoken", csrf),
            ("entity_type", kind), ("action", action), ("include_undo", "1"),
            *(("entity_ids[]", item) for item in ids),
            *(("rounds[]", item) for item in rounds),
            *more.items(),
        ]
        return request(port, update_url, method="POST", form=form,
                       extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"})

    with rust_server() as port:
        assert request(port, page_url)[1]["Location"].startswith("/public/login/")
        status, headers, page = request(port, page_url, cookie=helper_cookie)
        assert status == 200 and b"Expected Judges" in page and b"Check-In Team 0" in page
        assert b"Check-In Judge 0" in page and b"Check-In Room" in page
        assert request(port, page_url, cookie=board)[1]["Location"] == "/403/"
        assert request(port, "/pairings/status/", cookie=helper_cookie)[1]["Location"] == "/403/"
        csrf = load_cookies(headers)["csrftoken"].value
        assert post(port, board, csrf, ids=[teams[0].id])[1]["Location"] == "/403/"
        assert post(port, helper_cookie, "invalid", ids=[teams[0].id])[0] == 403
        first = json.loads(post(port, helper_cookie, csrf, ids=[teams[0].id, teams[1].id])[2])["undo"]
        assert first["count"] == 2
        assert list(Team.objects.values_list("checked_in", flat=True)) == [True, True]
        token = str(BulkCheckInChange.objects.get().token)
        assert first["id"] == token
        latest = json.loads(post(port, helper_cookie, csrf, action="check_out", ids=[teams[0].id])[2])["undo"]
        assert latest["count"] == 1
        assert post(port, helper_cookie, csrf, action="undo", change_id=token)[0] == 409
        preview_url = f"{page_url}?history=team&before={token}"
        preview = request(port, preview_url, cookie=helper_cookie)[2]
        assert b"Reverse 2 edits" in preview and b"Set 1 to Checked Out." in preview
        assert post(port, helper_cookie, csrf, action="restore", change_id=token, head=token)[0] == 409
        assert list(Team.objects.values_list("checked_in", flat=True)) == [False, True]

        assert post(port, helper_cookie, csrf, "judge", ids=[judges[0].id], rounds=[0, 1, 99])[0] == 200
        assert set(CheckIn.objects.values_list("round_number", flat=True)) == {0, 1}
        assert post(port, helper_cookie, csrf, "room", ids=[room.id], rounds=[2])[0] == 200
        assert RoomCheckIn.objects.get().round_number == 2
        expect = post(port, helper_cookie, csrf, "judge_expected", ids=[judges[1].id], rounds=[0, 1])
        assert expect[0] == 200
        expected_head = json.loads(expect[2])["undo"]["id"]
        assert JudgeExpectedCheckIn.objects.count() == 2
        assert PublicRosterChange.objects.filter(message__startswith="Changed availability for “Check-In Judge 1”", list_kind="judges").count() == 2

    with rust_server() as port:
        assert b"Undo last change (1 changed)" in request(port, page_url, cookie=helper_cookie)[2]
        restore = post(port, helper_cookie, csrf, action="restore", change_id=token, head=latest["id"])
        assert restore[1]["Location"] == f"{page_url}#team"
        assert list(Team.objects.values_list("checked_in", flat=True)) == [False, False]
        assert BulkCheckInChange.objects.filter(entity_type="team", undone=True).count() == 2
        assert b"Reversed" in request(port, f"{page_url}?history=team", cookie=helper_cookie)[2]
        assert post(port, helper_cookie, csrf, "judge_expected", "undo", change_id=expected_head)[0] == 200
        assert not JudgeExpectedCheckIn.objects.exists()
        assert PublicRosterChange.objects.filter(message__startswith="Changed availability for “Check-In Judge 1”", list_kind="judges").count() == 4
        assert CheckIn.objects.count() == 2 and RoomCheckIn.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_native_public_rankings_control_bounds_rounds_and_scores_for_board_and_anonymous():
    from mittab.apps.tab.models import PublicDisplaySetting
    from mittab.apps.tab.public_rankings import get_ranking_settings

    tab = legacy_tab_session("public-ranking-staff")
    board = legacy_tab_session("public-ranking-board")
    get_user_model().objects.get(username="public-ranking-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 3)
    TabSettings.set("tot_rounds", 2)
    TabSettings.set("debaters_public", 1)
    school = School.objects.create(name="Ranking School")
    names = ["Top Example", "Second Example", "Private Example", "Additional Example"]
    teams = [
        Team.objects.create(name=name, seed=Team.UNSEEDED, school=school, ranking_public=i != 2)
        for i, name in enumerate(names)
    ]
    people = [
        Debater.objects.create(name=f"Ranking Speaker {i}", novice_status=i % 2)
        for i in range(4)
    ]
    for team, speaker in zip(teams, people):
        team.debaters.add(speaker)
    first = Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1], victor=Round.GOV)
    private_debate = Round.objects.create(round_number=1, gov_team=teams[2], opp_team=teams[3], victor=Round.GOV)
    future = Round.objects.create(round_number=2, gov_team=teams[1], opp_team=teams[0], victor=Round.GOV)
    for debate, pair, score in ((first, (0, 1), "31.75"),
                                (private_debate, (2, 3), "28.00"),
                                (future, (1, 0), "35.00")):
        for rank, index in enumerate(pair, 1):
            RoundStats.objects.create(round=debate, debater=people[index],
                                      speaks=Decimal(score) - (rank - 1), ranks=rank)
    control = "/rankings/public-control/"
    standings = "/public/team-rankings/"
    speakers_page = "/public/speaker-rankings/"
    ballots = "/public/ballots/"
    paths = (standings, speakers_page, ballots)

    with rust_server() as port:
        for path in paths:
            assert request(port, path)[1]["Location"] == "/public/access-error/"
        status, headers, page = request(port, control, cookie=tab)
        assert status == 200 and b"Publish Round 1 ballots" in page
        csrf = load_cookies(headers)["csrftoken"].value

        def post(data, session=tab):
            return request(port, control, method="POST", form={"csrfmiddlewaretoken": csrf, **data},
                           extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"})

        policy = {"team_public": "on", "team_up_to_round": 1, "team_max_visible": 4,
                  "varsity_public": "on", "round_1_visible": "on", "round_1_include_ranks": "on"}
        assert post(policy, board)[1]["Location"] == "/403/"
        assert post(policy)[1]["Location"] == control
        assert get_ranking_settings("team")["up_to_round"] == 1
        assert PublicDisplaySetting.objects.get(slug="ballot_round_1").include_ranks
        teams_page = request(port, standings)[2]
        assert b"Top Example" in teams_page and b"Private Example" not in teams_page
        assert b"31.75" not in teams_page and b"Speaks</th>" not in teams_page
        vars_page = request(port, speakers_page)[2]
        assert b"Varsity Speakers" in vars_page and b"Novice Speakers" not in vars_page
        assert b"Ranking Speaker 0" in vars_page and b"Ranking Speaker 1" not in vars_page
        round_page = request(port, ballots)[2]
        assert b"Ranking Speaker 0" in round_page and b"Ranking Speaker 1" in round_page
        assert b"31.7500" not in round_page and b"Ranks" in round_page
        assert b"Private Example" not in round_page and b"Round 2</h3>" not in round_page
        for path in paths:
            anonymous = request(port, path)
            restricted = request(port, path, cookie=board)
            assert restricted[0] == anonymous[0] == 200
            assert restricted[1]["Cache-Control"] == "private, no-store"
            assert request(port, path, cookie=board, method="POST")[1]["Location"] == "/403/"
        assert post({**policy, "team_include_speaks": "on", "round_1_include_speaks": "on",
                     "varsity_include_speaks": "on"})[0] == 302
        assert b"31.75" in request(port, standings)[2]
        assert b"35.00" not in request(port, standings)[2]
        assert b"31.7500" in request(port, ballots)[2]
        assert b"Unadjusted speaks" in request(port, speakers_page)[2]


class BracketMarkup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.payload = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "div" and fields.get("id") == "brackets-viewer-container":
            self.payload = json.loads(fields["data-bracket"])


@pytest.mark.django_db(transaction=True)
def test_native_public_outrounds_match_the_visible_django_bracket_payload():
    from mittab.libs.bracket_display_logic import generate_bracket_data

    TabSettings.set("cur_round", 3)
    TabSettings.set("tot_rounds", 2)
    TabSettings.set("var_teams_visible", 8)
    TabSettings.set("debaters_public", 1)
    school = School.objects.create(name="Outrounds School")
    teams = {}
    for seed in range(1, 7):
        name = "Outround </script> Team" if seed == 5 else f"Outround Team {seed}"
        team = Team.objects.create(name=name, school=school, seed=Team.UNSEEDED)
        team.debaters.add(Debater.objects.create(name=f"Outround Speaker {seed}", novice_status=0))
        BreakingTeam.objects.create(team=team, seed=seed, effective_seed=seed, type_of_team=0)
        teams[seed] = team
    chair = Judge.objects.create(name="Public Outround Chair", rank=9)
    wing = Judge.objects.create(name="Public Outround Wing", rank=8)
    hidden = Judge.objects.create(name="Undisclosed Outround Judge", rank=10, wing_only=True)
    room = Room.objects.create(name="Public Outround Room", rank=10)
    first = Outround.objects.create(num_teams=8, gov_team=teams[5], opp_team=teams[4], victor=1, room=room)
    first.judges.add(chair, wing)
    Outround.objects.filter(id=first.id).update(chair=chair)
    other = Outround.objects.create(num_teams=8, gov_team=teams[3], opp_team=teams[6], victor=1)
    next_round = Outround.objects.create(num_teams=4, gov_team=teams[1], opp_team=teams[5])
    next_round.judges.add(hidden)
    board = legacy_tab_session("public-outs-board")
    get_user_model().objects.get(username="public-outs-board").groups.add(Group.objects.create(name="APDA Board"))
    varsity = "/public/outrounds/0/"
    novice = "/public/outrounds/1/"

    def payload(page):
        parser = BracketMarkup()
        parser.feed(page.decode())
        return parser.payload

    with rust_server() as port:
        assert request(port, novice)[1]["Location"] == "/public/access-error/"
        status, _, page = request(port, varsity, cookie=board)
        assert status == 200 and b"Round of 8" in page and b"Round of 4" not in page
        assert b"Advancing Teams" in page and b"<b>Public Outround Chair</b>" in page
        assert b"Undisclosed Outround Judge" not in page and b"Outround </script> Team" not in page
        pairings = list(Outround.objects.filter(pk__in=(first.id, other.id)).order_by("id"))
        assert payload(page) == json.loads(json.dumps(generate_bracket_data([{"rounds": pairings}])))
        Scratch.objects.create(team=teams[4], judge=chair, scratch_type=Scratch.TAB_SCRATCH)
        Judge.objects.filter(id=chair.id).update(rank=1, wing_only=True, request_wing=True)
        assert request(port, varsity, cookie=board)[2] == page
        assert request(port, varsity, cookie=board, method="POST")[1]["Location"] == "/403/"

        TabSettings.set("var_teams_visible", 4)
        updated = request(port, varsity)[2]
        assert b"Round of 4" in updated and b"Undisclosed Outround Judge" in updated
        expected = generate_bracket_data([{"rounds": pairings}, {"rounds": [next_round]}])
        assert payload(updated) == json.loads(json.dumps(expected))
        TabSettings.set("debaters_public", 0)
        confidential = request(port, varsity)[2]
        assert b"Outround Speaker" not in confidential
        assert b"Outround Team 1" in confidential
        TabSettings.set("outround_pairing_mode", 1)
        assert payload(request(port, varsity)[2]) is None


@pytest.mark.django_db(transaction=True)
def test_native_tab_cards_and_export_honor_board_and_public_release():
    tab = legacy_tab_session("tab-card-staff")
    board = legacy_tab_session("tab-card-board")
    board_user = get_user_model().objects.get(username="tab-card-board")
    board_user.groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 3)
    TabSettings.set("tot_rounds", 2)
    TabSettings.set("publish_tab_cards_with_first_outround", 1)
    school = School.objects.create(name="Card School")
    teams = [
        Team.objects.create(name=f"Card Team {i}", school=school, seed=Team.UNSEEDED)
        for i in range(2)
    ]
    people = [Debater.objects.create(name=f"Card Debater {i}", novice_status=i % 2) for i in range(3)]
    teams[0].debaters.add(*people[:2])
    teams[1].debaters.add(people[2])
    debate = Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1],
                                  victor=Round.GOV, rfd="private notes on the ballot")
    chair = Judge.objects.create(name="Card Judge Chair", rank=6, ballot_code="Synthetic-Cards-224")
    debate.judges.add(chair)
    Round.objects.filter(id=debate.id).update(chair=chair)
    for rank, person in enumerate(people, 1):
        RoundStats.objects.create(round=debate, debater=person,
                                  speaks=Decimal("32.25") - rank, ranks=rank)
    Bye.objects.create(round_number=2, bye_team=teams[0])
    NoShow.objects.create(round_number=2, no_show_team=teams[1])
    Outround.objects.create(num_teams=2, gov_team=teams[0], opp_team=teams[1])
    AuditEvent.record(debate, AuditEvent.MANUAL_CHAIR_CHANGE, board_user,
                      changes={"new_chair": chair.name, "remarks": "protected remarks fixture"},
                      note="protected audit fixture")
    public = "/public/tab-cards/"
    staff = "/all_tab_cards/"
    board_url = "/apda-board/tab-cards/"
    private_card = f"/team/card/{teams[0].id}/pretty/"
    board_card = f"/apda-board/tab-card/{teams[0].id}/"
    download = "/archive/black_rod_bundle/"

    with rust_server() as port:
        assert request(port, public)[1]["Location"] == "/public/access-error/"
        assert request(port, public, cookie=tab)[1]["Location"] == staff
        assert request(port, public, cookie=board)[1]["Location"] == "/403/"
        assert request(port, staff)[1]["Location"].startswith("/public/login/")
        assert request(port, staff, cookie=tab)[0] == 200
        assert request(port, board_url, cookie=tab)[0] == 403
        assert b"Choose a team" in request(port, board_url, cookie=board)[2]

        status, headers, page = request(port, board_card, cookie=board)
        assert status == 200 and headers["cache-control"] == "private, no-store"
        assert b"Card Debater 0" in page and b"Card Debater 2" not in page
        assert b"Byes" in page and b"New Chair" in page
        assert b"private notes" not in page and b"protected" not in page
        assert request(port, private_card, cookie=board)[2] == page
        assert request(port, private_card, cookie=board, method="POST")[1]["Location"] == "/403/"
        Scratch.objects.create(team=teams[0], judge=chair, scratch_type=Scratch.TAB_SCRATCH)
        Judge.objects.filter(id=chair.id).update(rank=9, wing_only=True)
        assert request(port, board_card, cookie=board)[2] == page

        assert request(port, download)[1]["Location"].startswith("/public/login/")
        status, headers, archive = request(port, download, cookie=board)
        assert status == 200 and headers["Content-Disposition"].endswith("-black-rod-bundle.json")
        bundle = json.loads(archive)
        assert set(bundle) == {"schema_version", "source", "exported_at", "tournament_name",
                               "schools", "debaters", "rounds"}
        assert bundle["rounds"][0]["judges"] == [{"original_name": chair.name, "is_chair": True}]
        assert b"private notes" not in archive and b"protected" not in archive
        assert b"Synthetic-Cards" not in archive and b"scratches" not in archive
        assert request(port, download, cookie=board, method="POST")[1]["Location"] == "/403/"

        TabSettings.set("var_teams_visible", 2)
        status, _, page = request(port, public)
        assert status == 200 and b"Card Debater 2" in page and b"No-shows" in page
        assert b"31.25" in page and b"Card Judge Chair" in page
        assert b"Manual edits" not in page and b"private notes" not in page
        TabSettings.set("publish_tab_cards_with_first_outround", 0)
        assert request(port, public)[1]["Location"] == "/public/access-error/"


def multipart_request(port, path, *, fields, file, cookie, file_field="file"):
    boundary = "mittab-rust-test-boundary"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    filename, content = file
    body.extend(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
         'Content-Type: application/octet-stream\r\n\r\n').encode(),
    )
    body.extend(content)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        client.request("POST", path, body=bytes(body), headers={
            "Cookie": cookie,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
        reply = client.getresponse()
        return reply.status, reply.headers, reply.read()
    finally:
        client.close()


@pytest.mark.django_db(transaction=True)
def test_native_board_apda_id_forms_and_spreadsheets_update_only_ids():
    from django.core.files.uploadedfile import SimpleUploadedFile
    from mittab.libs.spreadsheets import read_rows, spreadsheet_response

    tab = legacy_tab_session("apda-ids-staff")
    board = legacy_tab_session("apda-ids-board")
    get_user_model().objects.get(username="apda-ids-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    first = School.objects.create(name="Example, East", apda_id=-1)
    second = School.objects.create(name="École de l’Ouest", apda_id=8)
    debater = Debater.objects.create(name="Example Speaker", novice_status=Debater.VARSITY,
                                     apda_id=-1, email="private@example.invalid")
    teams = [
        Team.objects.create(name=f"APDA ID Team {i}", school=school, seed=Team.UNSEEDED)
        for i, school in enumerate((first, second))
    ]
    teams[0].debaters.add(debater)
    Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1], victor=Round.GOV)
    home = "/apda-board/"
    school_path = f"{home}school/{first.id}/"
    debater_path = f"{home}debater/{debater.id}/"
    school_import = f"{home}schools/import/"
    debater_import = f"{home}debaters/import/"
    school_export = f"{home}schools/export/"
    debater_export = f"{home}debaters/export/"
    with rust_server() as port:
        assert request(port, school_export, cookie=tab)[0] == 403
        status, headers, page = request(port, home, cookie=board)
        assert status == 200 and b"Example, East" in page
        assert b"private@example.invalid" not in page
        csrf = load_cookies(headers)["csrftoken"].value
        jar = f"sessionid={board}; csrftoken={csrf}"
        assert b"APDA ID Team 0" in request(port, school_path, cookie=board)[2]
        assert b"private@example.invalid" not in request(port, debater_path, cookie=board)[2]

        def update(path, **fields):
            return request(port, path, method="POST", extra_headers={"Cookie": jar},
                           form={"csrfmiddlewaretoken": csrf, **fields})

        assert update(school_path, apda_id=12, name="Other name")[0] == 422
        assert update(school_path, apda_id=12)[0] == 302
        assert update(school_path, apda_id=12)[0] == 302
        assert update(debater_path, apda_id=77)[0] == 302
        first.refresh_from_db()
        debater.refresh_from_db()
        assert (first.name, first.apda_id) == ("Example, East", 12)
        assert (debater.name, debater.apda_id, debater.email) == ("Example Speaker", 77, "private@example.invalid")
        for format in ("csv", "xls", "xlsx"):
            for path, kind, query in ((school_export, "school", [[name, str(number)] for name, number in School.objects.order_by("name").values_list("name", "apda_id")]),
                                      (debater_export, "debater", [[debater.name, "77"]])):
                status, headers, body = request(port, f"{path}?format={format}", cookie=board)
                assert status == 200
                assert headers["Content-Disposition"].endswith(f".{format}")
                file = SimpleUploadedFile(f"native.{format}", body)
                assert [cells for _, cells in read_rows(file)] == [[kind, "id"], *query]
        invalid = multipart_request(
            port, school_import, fields={"csrfmiddlewaretoken": csrf}, cookie=jar,
            file=("invalid.csv", f'school,id\r\n"{first.name}",99\r\n{second.name},bad\r\n'.encode()),
        )
        assert invalid[0] == 422
        first.refresh_from_db()
        assert first.apda_id == 12

        for index, format in enumerate(("csv", "xls", "xlsx"), 1):
            number = 100 + index
            valid = spreadsheet_response("test", format, ["APDA ID", "School name"],
                                         [[number, first.name], [99, "Missing Example"]]).content
            status, headers, _ = multipart_request(
                port, school_import, fields={"csrfmiddlewaretoken": csrf}, cookie=jar,
                file=(f"valid.{format}", valid),
            )
            assert status == 302
            assert headers["Location"] == f"{home}?ids_updated=1&rows_ignored=1"
            first.refresh_from_db()
            assert first.apda_id == number

        valid = b"Debater name,APDA ID\r\nExample Speaker,199\r\n"
        assert multipart_request(port, debater_import, fields={"csrfmiddlewaretoken": "invalid"},
                                 cookie=jar, file=("valid.csv", valid))[0] == 403
        assert multipart_request(port, debater_import, fields={"csrfmiddlewaretoken": csrf, "email": "hidden"},
                                 cookie=jar, file=("valid.csv", valid))[0] == 422
        assert multipart_request(port, debater_import, fields={"csrfmiddlewaretoken": csrf},
                                 cookie=jar, file=("valid.csv", valid))[0] == 302
        debater.refresh_from_db()
        assert debater.apda_id == 199 and debater.email == "private@example.invalid"


@pytest.mark.django_db(transaction=True)
def test_native_team_portal_locks_changes_at_first_pairing_and_protects_tab_scratches():
    code = "Synthetic-Team-01"
    TabSettings.set("cur_round", 1)
    TabSettings.set("tot_rounds", 2)
    TabSettings.set("judges_public", 1)
    options, _ = RegistrationConfig.objects.update_or_create(
        id=1, defaults={"team_name_changes_allowed": True, "disc_scratches_open": True, "disc_scratch_quantity": 2},
    )
    school = School.objects.create(name="Team Portal School")
    team = Team.objects.create(name="Original Team", school=school, seed=Team.HALF_SEED, team_code=code)
    opposite = Team.objects.create(name="Opponent Team", school=school, seed=Team.UNSEEDED)
    team.debaters.add(Debater.objects.create(name="Team Portal Debater", novice_status=Debater.VARSITY))
    allowed = Judge.objects.create(name="Available to scratch", rank=4)
    hidden = Judge.objects.create(name="Tab blocked judge", rank=8)
    assigned = Judge.objects.create(name="Previously assigned chair", rank=8)
    room = Room.objects.create(name="Previously assigned room", rank=4)
    tab_scratch = Scratch.objects.create(team=team, judge=hidden, scratch_type=Scratch.TAB_SCRATCH)
    board = legacy_tab_session("team-portal-board")
    get_user_model().objects.get(username="team-portal-board").groups.add(Group.objects.create(name="APDA Board"))
    search = "/team_portal/"
    path = f"{search}{code}/"

    with rust_server() as port:
        status, headers, body = request(port, search)
        assert status == 200 and b"Team code" in body
        assert headers["Referrer-Policy"] == "same-origin"
        csrf = load_cookies(headers)["csrftoken"].value

        def post(url, form, *, token=csrf, session=None):
            session = f"; sessionid={session}" if session else ""
            return request(port, url, method="POST", form={"csrfmiddlewaretoken": token, **form},
                           extra_headers={"Cookie": f"csrftoken={csrf}{session}"})

        assert post(search, {"team_code": code.lower()})[1]["Location"] == path
        assert post(search, {"team_code": "unknown"})[0] == 422
        assert request(port, f"{search}unknown/")[0] == 404
        status, _, body = request(port, path)
        assert status == 200 and b"Team Portal Debater" in body
        assert b"Half seed" in body and b"Available to scratch" in body
        assert b"Tab blocked judge" not in body
        assert request(port, path, cookie=board)[0] == 403

        name = {"action": "update_name", "name": "Renamed Team"}
        assert post(path, name, token="invalid")[0] == 403
        assert post(path, {**name, "name": opposite.name})[0] == 422
        assert post(path, name)[1]["Location"] == path
        old_coin = team.tiebreaker
        team.refresh_from_db()
        assert team.name == "Renamed Team" and team.tiebreaker == old_coin
        fields = {"action": "update_scratches", "scratch_0": allowed.id}
        assert post(path, {**fields, "scratch_1": allowed.id})[0] == 422
        assert post(path, {**fields, "scratch_1": hidden.id})[0] == 422
        assert not Scratch.objects.filter(team=team, scratch_type=Scratch.TEAM_SCRATCH).exists()
        assert post(path, fields)[1]["Location"] == path
        assert Scratch.objects.filter(team=team, judge=allowed, scratch_type=Scratch.TEAM_SCRATCH).exists()
        assert Scratch.objects.filter(pk=tab_scratch.id).exists()
        TabSettings.set("judges_public", 0)
        assert b"Available to scratch" not in request(port, path)[2]
        assert post(path, fields)[0] == 422
        TabSettings.set("judges_public", 1)
        TabSettings.set("cur_round", 3)
        first = Round.objects.create(round_number=1, gov_team=team, opp_team=opposite, room=room)
        first.judges.add(assigned)
        Round.objects.filter(pk=first.id).update(chair=assigned)
        Round.objects.create(round_number=2, gov_team=opposite, opp_team=team, victor=Round.GOV)
        body = request(port, path)[2]
        assert b"Previous Pairings" in body and b"Previously assigned chair" in body
        assert b"Previously assigned room" in body and b"Opponent Team" in body
        assert b"Save Scratches" not in body and b"Save Team Name" not in body
        assert post(path, {**name, "name": "Late Change"})[0] == 422
        assert post(path, {"action": "update_scratches"})[0] == 422
        for guarded in (path, search):
            assert request(port, guarded, cookie=board)[1]["Location"] == "/403/"
            assert post(guarded, name, session=board)[1]["Location"] == "/403/"
        team.refresh_from_db()
        assert team.name == "Renamed Team"
        assert set(Scratch.objects.filter(team=team).values_list("judge", flat=True)) == {allowed.id, hidden.id}
    options.refresh_from_db()
    assert options.disc_scratch_quantity == 2


@contextmanager
def mock_registration_directory(deliveries=None):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class DirectoryHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            data = {
                "/api/schools/all/": [{"id": 71, "name": "North Institute"}],
                "/api/debaters/71/": [{"apda_id": 8101, "name": "Test First", "novice_status": 1}],
            }
            if self.path not in data:
                self.send_error(404)
                return
            self.reply(data[self.path])

        def do_POST(self):
            if self.path == "/v2/email/outbound-emails" and deliveries is not None:
                signature = self.headers.get("Authorization", "")
                if not signature.startswith("AWS4-HMAC-SHA256 Credential=fixture-access/") or "/ses/aws4_request" not in signature:
                    self.send_error(403)
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                deliveries.append(body)
                self.reply({"MessageId": "example"})
                return
            if self.path != "/api/private/debater-emails/" or self.headers.get("Authorization") != "Bearer fixture":
                self.send_error(403)
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if body["debater_ids"] != [8101]:
                self.send_error(422)
                return
            self.reply({"debaters": [{"id": 8101, "email": "hidden-fixture-address@example.test"}]})

        def reply(self, payload):
            content = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), DirectoryHandler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join(timeout=5)


@pytest.mark.django_db(transaction=True)
def test_native_registration_create_edit_privacy_and_delete():
    from mittab.apps.registration.models import Registration, RegistrationChangeLog, RegistrationLink

    tab = legacy_tab_session("registration-staff")
    board = legacy_tab_session("registration-board")
    get_user_model().objects.get(username="registration-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 3)
    TabSettings.set("tot_rounds", 3)
    school = School.objects.create(name="North Institute", apda_id=71)
    secondary = School.objects.create(name="South Institute")
    options, _ = RegistrationConfig.objects.update_or_create(id=1, defaults={"crash_housing_enabled": True})
    RegistrationLink.objects.create(title="Join the Discord", url="https://example.test/confirm", description="Schedule and updates")
    prepared = rust_command("migrate", connection.settings_dict["NAME"])
    assert prepared.returncode == 0, prepared.stderr
    path = "/registration/"
    manage = "/registration/manage/"
    ready = {
        "school": "apda:71",
        "email": "school@example.test",
        "dietary_requests": "No dairy",
        "housing_requests": "Two students",
        "teams-TOTAL_FORMS": "1",
        "teams-0-name": "Hybrid Example",
        "teams-0-seed_choice": "2",
        "teams-0-team_school_source": "debater_one",
        "teams-0-debater_one_name": "Test First",
        "teams-0-debater_one_school": "apda:71",
        "teams-0-debater_one_apda_id": "8101",
        "teams-0-debater_one_novice_status": "1",
        "teams-0-debater_one_use_private_email": "on",
        "teams-0-debater_one_requests_wgm_housing": "on",
        "teams-0-debater_two_name": "Test Second",
        "teams-0-debater_two_email": "second@example.test",
        "teams-0-debater_two_school": f"pk:{secondary.id}",
        "judges-TOTAL_FORMS": "1",
        "judges-0-name": "Example Judge",
        "judges-0-email": "judge@example.test",
        "judges-0-experience": "7",
        "judges-0-availability_outround": "on",
        "judges-0-availability_round_2": "on",
    }
    with mock_registration_directory() as endpoint, rust_server(overrides={
            "MITTAB_SCHOOL_DIRECTORY_URL": f"{endpoint}/api/schools/all/",
            "BLACK_ROD_API_BASE_URL": endpoint,
            "BLACK_ROD_PRIVATE_API_TOKEN": "fixture",
    }) as port:
        status, headers, body = request(port, path)
        assert status == 200 and b"North Institute" in body and b"Add team" in body
        assert headers["referrer-policy"] == "same-origin"
        csrf = load_cookies(headers)["csrftoken"].value

        def post(url, fields, *, session=None, token=csrf):
            jar = (f"sessionid={session}; " if session else "") + f"csrftoken={csrf}"
            return request(port, url, method="POST", extra_headers={"Cookie": jar},
                           form=[("csrfmiddlewaretoken", token), *fields])

        def email_status(school_id):
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                client.request("POST", "/registration/api/debater-email-status/", body=json.dumps(
                    {"school_id": school_id, "debater_id": 8101}), headers={"Cookie": f"csrftoken={csrf}",
                    "X-CSRFToken": csrf, "Content-Type": "application/json"})
                response = client.getresponse()
                return response.status, json.loads(response.read())
            finally:
                client.close()

        assert email_status(71) == (200, {"id": 8101, "has_email": True, "masked_email": "hi***@example.test"})
        assert email_status(72) == (404, {"id": 8101, "has_email": False, "masked_email": ""})
        assert request(port, path, cookie=board)[0] == 403
        assert post(path, ready.items(), token="invalid")[0] == 403
        for overrides in (
            {"teams-0-team_school_source": "debater_two"},
            {"teams-0-debater_one_apda_id": "9999"},
            {"teams-0-debater_one_id": "9999"},
            {"teams-TOTAL_FORMS": "201"},
        ):
            response = post(path, {**ready, **overrides}.items())
            assert response[0] == 422, response
            assert not Registration.objects.exists() and not Team.objects.exists()
        payload = [*ready.items(), ("judges-0-schools", f"pk:{secondary.id}"), ("judges-0-schools", "apda:71")]
        status, headers, body = post(path, payload)
        assert status == 302, (status, body)
        target = headers["Location"]
        edit = target.split("?")[0]
        registration = Registration.objects.get()
        assert target == f"/registration/{registration.herokunator_code}/?saved=1&email=unavailable"
        team = registration.teams.get()
        assert (team.school_id, team.hybrid_school_id, team.checked_in, team.seed) == (school.id, secondary.id, False, 2)
        first = team.debaters.get(apda_id=8101)
        second = team.debaters.exclude(id=first.id).get()
        assert first.email == "hidden-fixture-address@example.test" and first.novice_status == 1
        assert first.needs_crash_housing and first.requests_wgm_housing
        judge = registration.judges.get()
        assert judge.ballot_code and team.team_code
        assert judge.reported_experience == 7 and judge.rank == 0
        assert set(judge.schools.values_list("id", flat=True)) == {school.id, secondary.id}
        assert list(judge.expected_checkins.order_by("round_number").values_list("round_number", flat=True)) == [0, 2]
        assert registration.change_logs.get().action == "created"
        status, _, body = request(port, target)
        assert status == 200 and b"Join the Discord" in body and b"Hybrid Example" in body
        assert b"Using the email on file" in body and b"hidden-fixture-address" not in body
        assert first.email not in json.dumps(registration.change_logs.get().snapshot)
        revision = re.search(rb'name="revision" value="([^"]+)"', body).group(1).decode()
        assert post("/registration/edit/", [("registration_code", registration.herokunator_code)])[1]["Location"] == edit

        Judge.objects.filter(id=judge.id).update(rank=8.5, wing_only=True)
        update = {
            **ready, "revision": revision, "teams-0-team_id": team.id, "teams-0-name": "Renamed Hybrid",
            "teams-0-debater_one_id": first.id, "teams-0-debater_two_id": second.id,
            "teams-0-debater_two_school": "apda:71", "judges-0-judge_id": judge.id, "judges-0-experience": 9,
        }
        coin = team.tiebreaker
        assert post(edit, update.items())[0] == 302
        assert post(edit, update.items())[0] == 422
        team.refresh_from_db()
        judge.refresh_from_db()
        assert (team.name, team.tiebreaker, team.hybrid_school_id) == ("Renamed Hybrid", coin, secondary.id)
        assert (judge.rank, judge.wing_only, judge.reported_experience) == (Decimal("8.50"), True, 9)
        assert registration.change_logs.filter(action="updated").exists()
        assert request(port, manage, cookie=tab)[0] == 200
        assert request(port, manage, cookie=board)[0] == 403
        assert post(manage, [("registration_id", registration.id)], session=board)[0] == 403

        options.allow_registration_edits = False
        options.save(update_fields=["allow_registration_edits"])
        assert b"Registration updates are currently disabled." in request(port, edit)[2]
        assert post(edit, update.items())[0] == 422
        assert post(manage, [("registration_id", registration.id)], session=tab)[1]["Location"] == f"{manage}?saved=2"
        assert not Registration.objects.exists() and not Team.objects.exists() and not Judge.objects.exists()
        assert not Debater.objects.filter(id__in=(first.id, second.id)).exists()
        assert RegistrationChangeLog.objects.filter(action="deleted", registration_id=None).exists()
        assert request(port, edit)[0] == 404


@pytest.mark.django_db(transaction=True)
def test_native_registration_schedule_and_get_links():
    from mittab.apps.registration.models import RegistrationLink

    tab = legacy_tab_session("registration-schedule")
    TabSettings.set("cur_round", 1)
    TabSettings.set("judges_public", 0)
    manage = "/registration/manage/"
    target = "/registration/manage/links/registration/"
    with rust_server(overrides={"MITTAB_SCHOOL_DIRECTORY_URL": ""}) as port:
        status, headers, body = request(port, manage, cookie=tab)
        assert status == 200 and b"Eastern Time" in body
        csrf = load_cookies(headers)["csrftoken"].value

        def post(url, form):
            return request(port, url, method="POST", extra_headers={"Cookie": f"sessionid={tab}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": csrf, **form})

        values = {
            "allow_new_registrations": "on", "allow_registration_edits": "on",
            "new_registrations_open_at": "2026-10-15T16:00", "new_registrations_close_at": "2026-11-15T10:00",
            "disc_scratches_open": "on", "disc_scratch_quantity": "3",
        }
        assert post(manage, values)[0] == 422
        assert TabSettings.get("judges_public", use_cache=False) == 0
        for change in ({"new_registrations_open_at": "2026-11-01T01:30"},
                       {"new_registrations_close_at": "2026-09-15T12:00"}):
            assert post(manage, {**values, **change, "confirm_judge_list_publication": "on"})[0] == 422
        assert post(manage, {**values, "confirm_judge_list_publication": "on"})[0] == 302
        config = RegistrationConfig.objects.get()
        assert config.disc_scratch_quantity == 3 and config.disc_scratches_open
        assert config.new_registrations_open_at.isoformat() == "2026-10-15T20:00:00+00:00"
        assert config.new_registrations_close_at.isoformat() == "2026-11-15T15:00:00+00:00"
        assert TabSettings.get("judges_public", use_cache=False) == 1
        assert b'value="2026-10-15T16:00"' in request(port, manage, cookie=tab)[2]
        assert b"New registrations are currently closed" in request(port, "/registration/")[2]
        link = {"title": "Example Resource", "url": "https://example.test/link", "is_active": "on", "display_order": "1"}
        assert post(target, {**link, "url": "javascript:alert(1)"})[1]["Location"] == f"{manage}?error=1"
        assert not RegistrationLink.objects.exists()
        assert post(target, link)[1]["Location"] == f"{manage}?saved=3"
        row = RegistrationLink.objects.get()
        assert post(f"{target}{row.id}/", {**link, "title": "Updated Resource"})[0] == 302
        assert b"Updated Resource" in request(port, manage, cookie=tab)[2]
        assert post(f"{target}{row.id}/delete/", {})[0] == 302
        assert not RegistrationLink.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_native_public_homepage_branding_logo_and_resources():
    from io import BytesIO
    from PIL import Image

    tab = legacy_tab_session("homepage-admin-tab")
    board = legacy_tab_session("homepage-admin-board")
    get_user_model().objects.get(username="homepage-admin-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 1)
    TabSettings.set("tournament_name", "Original Tournament")
    prepared = rust_command("migrate", connection.settings_dict["NAME"])
    assert prepared.returncode == 0, prepared.stderr
    page = "/settings/public-homepage/"
    with rust_server() as port:
        status, headers, body = request(port, page, cookie=tab)
        assert status == 200 and b"Original Tournament" in body
        assert request(port, page, cookie=board)[0] == 403
        csrf = load_cookies(headers)["csrftoken"].value
        jar = f"sessionid={tab}; csrftoken={csrf}"
        fields = {"csrfmiddlewaretoken": csrf, "tournament_name": "Example Branding", "theme_color": "#F0C020"}
        invalid = multipart_request(port, page, fields=fields, file=("wrong.png", b"not an image"),
                                    cookie=jar, file_field="tournament_logo")
        assert invalid[0] == 422
        assert TabSettings.get("tournament_name", use_cache=False) == "Original Tournament"
        assert b"#00438A" in request(port, "/theme.css")[2]
        converted = BytesIO()
        Image.new("RGB", (42, 26), color=(100, 140, 180)).save(converted, format="JPEG")
        valid = multipart_request(port, page, fields=fields, file=("logo.jpg", converted.getvalue()),
                                  cookie=jar, file_field="tournament_logo")
        assert valid[0] == 302 and valid[1]["Location"] == page, valid
        assert TabSettings.get("tournament_name", use_cache=False) == "Example Branding"
        assert TabSettings.get("theme_color", use_cache=False) == "#F0C020"
        _, headers, css = request(port, "/theme.css")
        assert headers["Content-Type"].startswith("text/css") and b"#F0C020" in css and b"#1a1a1a" in css
        status, headers, logo = request(port, "/tournament-logo/")
        assert status == 200 and headers["Content-Type"] == "image/png"
        assert Image.open(BytesIO(logo)).size == (42, 26)
        assert request(port, "/favicon.ico")[2] == logo

        def post(path, fields):
            return request(port, path, method="POST", extra_headers={"Cookie": jar},
                           form={"csrfmiddlewaretoken": csrf, **fields})

        path = "/registration/manage/info-text/"
        assert post(path, {"title": "GA", "text": "Main library room 301", "is_active": "on"})[0] == 302
        assert b"Main library room 301" in request(port, "/public/")[2]
        line = InfoText.objects.get()
        assert post(f"{path}{line.id}/", {"title": "GA", "text": "Second room", "is_active": "on"})[0] == 302
        assert b"Second room" in request(port, "/public/")[2]
        assert post(f"{path}{line.id}/delete/", {})[0] == 302
        assert b"Second room" not in request(port, "/public/")[2]
        assert post("/registration/manage/links/info/", {"title": "Handbook", "url": "https://example.test/guide",
                                                        "is_active": "on"})[1]["Location"].startswith(page)
        assert b"Handbook" in request(port, "/public/")[2]


@pytest.mark.django_db(transaction=True)
def test_native_registration_delivers_confirmation_and_codes_via_ses():
    from mittab.apps.registration.models import Registration, RegistrationConfirmationEmailLog
    from mittab.apps.tab.models import JudgeCodeEmailLog, TeamCodeEmailLog

    TabSettings.set("cur_round", 1)
    TabSettings.set("tournament_name", "Email Test & Tournament")
    school = School.objects.create(name="Email Test School")
    prepared = rust_command("migrate", connection.settings_dict["NAME"])
    assert prepared.returncode == 0, prepared.stderr
    deliveries = []
    with mock_registration_directory(deliveries) as fixture, rust_server(overrides={
            "MITTAB_SCHOOL_DIRECTORY_URL": "",
            "AWS_SES_ACCESS_KEY_ID": "fixture-access",
            "AWS_SES_SECRET_ACCESS_KEY": "fixture-secret",
            "MITTAB_SES_ENDPOINT": fixture,
            "EMAIL_FROM_NAME": "Example Tab",
            "EMAIL_FROM_ADDRESS": "tab@example.test",
            "EMAIL_REPLY_TO": "reply@example.test",
            "AWS_SES_CONFIGURATION_SET": "fixture-config",
            "MITTAB_PUBLIC_URL": "https://tournament.example.test",
    }) as port:
        path = "/registration/"
        status, headers, _ = request(port, path)
        assert status == 200
        csrf = load_cookies(headers)["csrftoken"].value
        fields = {
            "csrfmiddlewaretoken": csrf,
            "school": f"pk:{school.id}",
            "email": "contact@example.test",
            "teams-TOTAL_FORMS": "1",
            "teams-0-name": "Example & Team",
            "teams-0-debater_one_name": "First Member",
            "teams-0-debater_one_email": "first@example.test",
            "teams-0-debater_one_school": f"pk:{school.id}",
            "teams-0-debater_two_name": "Second Member",
            "teams-0-debater_two_email": "second@example.test",
            "teams-0-debater_two_school": f"pk:{school.id}",
            "judges-TOTAL_FORMS": "1",
            "judges-0-name": "Judge Example",
            "judges-0-experience": "3",
            "judges-0-email": "judge@example.test",
        }
        jar = {"Cookie": f"csrftoken={csrf}"}
        status, headers, _ = request(port, path, method="POST", form=fields, extra_headers=jar)
        registration = Registration.objects.get()
        assert status == 302 and headers["Location"] == f"/registration/{registration.herokunator_code}/?saved=1"
        team = registration.teams.get()
        judge = registration.judges.get()
        expected = {
            "contact@example.test": f"/registration/{registration.herokunator_code}/",
            "judge@example.test": f"/public/e-ballots/{judge.ballot_code}/",
            "first@example.test": f"/team_portal/{team.team_code}/",
            "second@example.test": f"/team_portal/{team.team_code}/",
        }
        assert len(deliveries) == 4
        for message in deliveries:
            assert message["FromEmailAddress"] == "Example Tab <tab@example.test>"
            assert message["ReplyToAddresses"] == ["reply@example.test"]
            assert message["ConfigurationSetName"] == "fixture-config"
            recipient = message["Destination"]["ToAddresses"][0]
            body = message["Content"]["Simple"]["Body"]
            assert f"https://tournament.example.test{expected.pop(recipient)}" in body["Text"]["Data"]
            assert "href=" in body["Html"]["Data"]
            assert "Example & Team" not in body["Html"]["Data"]
        assert not expected
        assert RegistrationConfirmationEmailLog.objects.get().successful
        assert TeamCodeEmailLog.objects.filter(team=team, successful=True, source="registration").count() == 2
        assert JudgeCodeEmailLog.objects.filter(judge=judge, source="registration").count() == 1

        fields["teams-0-team_id"] = team.id
        fields["teams-0-debater_one_id"] = team.debaters.get(name="First Member").id
        fields["teams-0-debater_two_id"] = team.debaters.get(name="Second Member").id
        fields["judges-0-judge_id"] = judge.id
        status, _, _ = request(port, f"/registration/{registration.herokunator_code}/",
                               method="POST", form=fields, extra_headers=jar)
        assert status == 302
        assert len(deliveries) == 5
        assert TeamCodeEmailLog.objects.count() == 2 and JudgeCodeEmailLog.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_native_staff_invites_and_legacy_password_links_are_single_use():
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode
    from mittab.apps.tab.models import StaffInviteEmailLog

    tab = legacy_tab_session("staff-invite-tab")
    board = legacy_tab_session("staff-invite-board")
    board_user = get_user_model().objects.get(username="staff-invite-board")
    board_user.email = "board@example.test"
    board_user.save(update_fields=["email"])
    board_user.groups.add(Group.objects.create(name="APDA Board"))
    older = get_user_model().objects.create(username="existing-staff", email="existing@example.test",
                                            is_active=True, is_staff=True)
    older.set_unusable_password()
    older.save()
    old_token = default_token_generator.make_token(older)
    old_path = f"/public/staff-invite/{urlsafe_base64_encode(force_bytes(older.id))}/{old_token}/"
    TabSettings.set("cur_round", 1)
    TabSettings.set("start_tournament_wizard_completed", 0)
    prepared = rust_command("migrate", connection.settings_dict["NAME"])
    assert prepared.returncode == 0, prepared.stderr
    deliveries = []
    with mock_registration_directory(deliveries) as fixture, rust_server(overrides={
            "MITTAB_SCHOOL_DIRECTORY_URL": "",
            "AWS_SES_ACCESS_KEY_ID": "fixture-access",
            "AWS_SES_SECRET_ACCESS_KEY": "fixture-secret",
            "MITTAB_SES_ENDPOINT": fixture,
            "EMAIL_FROM_ADDRESS": "tab@example.test",
            "MITTAB_PUBLIC_URL": "https://tournament.example.test",
    }) as port:
        start = "/setup/start-tournament/"
        invite = "/staff/invite/"
        status, headers, body = request(port, start, cookie=tab)
        assert status == 200 and b"Send Staff Invite" in body
        csrf = load_cookies(headers)["csrftoken"].value

        def post(path, fields, session=None, *, token=csrf, ajax=False):
            headers = {"Cookie": (f"sessionid={session}; " if session else "") + f"csrftoken={csrf}"}
            if ajax:
                headers["X-Requested-With"] = "XMLHttpRequest"
            return request(port, path, method="POST", extra_headers=headers,
                           form={"csrfmiddlewaretoken": token, **fields})

        new = {
            "action": "send_invite", "invite-email": "helper@example.test", "invite-username": "helper-example",
            "invite-permission_preset": "checkin_helper",
        }
        assert post(start, new, tab, token="invalid")[0] == 403
        assert post(start, new, board)[0] == 403
        denied = post(start, {**new, "invite-email": "board@example.test",
                              "invite-username": board_user.username}, tab, ajax=True)
        assert denied[0] == 400 and not deliveries
        sent = post(start, new, tab, ajax=True)
        assert sent[0] == 200 and json.loads(sent[2])["invite_count"] == 1
        assert TabSettings.get("start_tournament_staff_invites_sent", use_cache=False) == 1
        user = get_user_model().objects.get(username="helper-example")
        assert user.is_staff and user.is_active and not user.is_superuser and not user.has_usable_password()
        assert list(user.groups.values_list("name", flat=True)) == ["MIT-TAB: Check-in helper"]
        assert StaffInviteEmailLog.objects.get().successful
        assert len(deliveries) == 1
        payload = deliveries[0]["Content"]["Simple"]["Body"]["Text"]["Data"]
        native_path = re.search(r"https://tournament.example.test(/public/staff-invite/[^\s]+)", payload).group(1)
        status, headers, body = request(port, native_path)
        assert status == 200 and headers["Referrer-Policy"] == "same-origin" and b"Confirm password" in body
        setup = {"new_password1": "fixture_staff_phrase_87#", "new_password2": "fixture_staff_phrase_87#"}
        assert post(native_path, {**setup, "new_password2": "different"})[0] == 422
        for path in (native_path, old_path):
            status, _, body = request(port, path)
            assert status == 200 and b"Confirm password" in body
            accepted = post(path, setup)
            assert accepted[0] == 302 and accepted[1]["Location"] == "/public/staff-invite/complete/"
            assert b"already used" in request(port, path)[2]
            assert post(path, setup)[0] == 200
        user.refresh_from_db()
        older.refresh_from_db()
        assert user.check_password(setup["new_password1"]) and older.check_password(setup["new_password1"])

        TabSettings.set("start_tournament_wizard_completed", 1)
        assert post(start, new, tab, ajax=True)[0] == 400
        assert request(port, invite, cookie=tab)[0] == 200
        stand_alone = {"email": "assistant@example.test", "username": "example-assistant", "permission_preset": "tab_assistant"}
        assert post(invite, stand_alone, tab)[1]["Location"] == f"{invite}?sent=true"
        assert get_user_model().objects.get(username="example-assistant").groups.filter(name="MIT-TAB: Tab assistant").exists()
        assert len(deliveries) == 2


@pytest.mark.django_db(transaction=True)
def test_native_email_management_writes_history_and_sends_written_decisions():
    from mittab.apps.tab.models import WrittenRFDEmailLog

    tab = legacy_tab_session("email-management-tab")
    board = legacy_tab_session("email-management-board")
    get_user_model().objects.get(username="email-management-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 3)
    TabSettings.set("tot_rounds", 3)
    TabSettings.set("written_rfd_first_round", 2)
    school = School.objects.create(name="Example School")
    gov = Team.objects.create(name="Example Gov", school=school, seed=0)
    opp = Team.objects.create(name="Example Opp", school=school, seed=0)
    first = Debater.objects.create(name="First Debater", novice_status=0, email="speaker@example.test")
    second = Debater.objects.create(name="Second Debater", novice_status=0, email="SPEAKER@example.test")
    gov.debaters.add(first)
    opp.debaters.add(second)
    judge = Judge.objects.create(name="Example Chair", rank=5, ballot_code="bright-tree-abcdef")
    debate = Round.objects.create(round_number=2, gov_team=gov, opp_team=opp, chair=judge, victor=Round.GOV,
                                  rfd="Example <b>reason</b>")
    RoundStats.objects.create(round=debate, debater=first, speaks=Decimal("27"), ranks=1, debater_role="PM")
    Round.objects.filter(id=debate.id).update(rfd="Example <b>reason</b>")
    deliveries = []
    with mock_registration_directory(deliveries) as fixture, rust_server(overrides={
            "AWS_SES_ACCESS_KEY_ID": "fixture-access", "AWS_SES_SECRET_ACCESS_KEY": "fixture-secret",
            "MITTAB_SES_ENDPOINT": fixture, "EMAIL_FROM_ADDRESS": "tab@example.test",
            "MITTAB_PUBLIC_URL": "https://tournament.example.test",
    }) as port:
        path = "/email_management/"
        status, headers, body = request(port, path, cookie=tab)
        assert status == 200 and b"Example Gov vs Example Opp" in body, (status, body)
        csrf = load_cookies(headers)["csrftoken"].value
        assert request(port, path, cookie=board)[0] == 403
        assert request(port, "/download_judge_codes/", cookie=board)[0] == 403
        assert b"Example Chair: bright-tree-abcdef" in request(port, "/download_judge_codes/", cookie=tab)[2]

        def post(session, token=csrf):
            return request(port, path, method="POST",
                           extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form=[("csrfmiddlewaretoken", token), ("email_action", "written_rfds"),
                                 ("round_ids", debate.id), ("round_ids", debate.id)])

        assert post(board)[0] == 403
        assert post(tab, token="invalid")[0] == 403
        status, headers, _ = post(tab)
        assert status == 302 and headers["Location"] == f"{path}?sent=1"
        assert len(deliveries) == 1
        message = deliveries[0]["Content"]["Simple"]
        assert "Example <b>reason</b>" in message["Body"]["Text"]["Data"]
        assert "Example &lt;b&gt;reason&lt;/b&gt;" in message["Body"]["Html"]["Data"]
        assert WrittenRFDEmailLog.objects.get().email.lower() == "speaker@example.test"
        assert b"Delivery History" in request(port, path, cookie=tab)[2]


@pytest.mark.django_db(transaction=True)
def test_native_staff_roster_edits_and_data_entry_scope():
    tab = legacy_tab_session("native-roster-tab")
    board = legacy_tab_session("native-roster-board")
    get_user_model().objects.get(username="native-roster-board").groups.add(Group.objects.create(name="APDA Board"))
    helper = get_user_model().objects.create_user(username="native-roster-helper", is_staff=True)
    helper.groups.add(Group.objects.create(name="MIT-TAB: Data entry helper"))
    django = Client()
    django.force_login(helper)
    helper = django.cookies["sessionid"].value
    TabSettings.set("cur_round", 1)
    TabSettings.set("tot_rounds", 2)
    tag = RoomTag.objects.create(tag="Accessible", priority="1.00")

    with rust_server() as port:
        status, headers, body = request(port, "/enter_school/", cookie=tab)
        assert status == 200 and b"Create School" in body, (status, body)
        csrf = load_cookies(headers)["csrftoken"].value

        def post(path, values, session=tab, token=csrf):
            return request(port, path, method="POST", extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form=[("csrfmiddlewaretoken", token), *values])

        fields = {"name": "Roster School", "apda_id": 445}
        assert post("/enter_school/", fields.items(), token="bad")[0] == 403
        assert post("/enter_school/", fields.items(), session=board)[0] == 403
        response = post("/enter_school/", fields.items(), session=helper)
        assert response[0] == 302, response
        school = School.objects.get(name="Roster School")
        assert school.apda_id == 445
        assert post("/enter_school/", fields.items())[0] == 422
        assert request(port, "/", cookie=helper)[1]["Location"] == "/view_teams/"
        assert request(port, "/view_schools/", cookie=helper)[0] == 200
        assert request(port, "/registration/manage/", cookie=helper)[1]["Location"] == "/403/"

        for name in ("Example Speaker One", "Example Speaker Two"):
            response = post("/enter_debater/", {"name": name, "school": school.id, "novice_status": 1,
                                                "email": "example@example.test"}.items(), session=helper)
            assert response[0] == 302, response
        first, second = list(Debater.objects.order_by("id"))
        team_path = "/enter_team/"
        team_fields = [("school", school.id), ("name", "Roster Team"), ("seed", 2), ("debaters", first.id),
                       ("debaters", second.id), ("required_room_tags", tag.id), ("ranking_public", "on")]
        assert post(team_path, team_fields, session=helper)[0] == 302
        team = Team.objects.get(name="Roster Team")
        assert team.team_code and team.tiebreaker and team.created_by.username == "native-roster-helper"
        assert team.required_room_tags.get() == tag and team.checked_in
        assert set(team.debaters.values_list("id", flat=True)) == {first.id, second.id}
        assert post(team_path, [(key, "Different Team" if key == "name" else value) for key, value in team_fields], session=helper)[0] == 422
        path = f"/team/{team.id}/"
        assert b'name="checked_in"' not in request(port, path, cookie=helper)[2]
        assert b'name="checked_in"' in request(port, path, cookie=tab)[2]
        assert post(path, team_fields, session=helper)[0] == 302
        team.refresh_from_db()
        assert team.checked_in
        assert post(f"/debater/{second.id}/delete/", [], session=helper)[0] == 422
        assert post(f"/school/{school.id}/delete/", [], session=helper)[0] == 422

        judge = [("name", "Roster Judge"), ("rank", "5.50"), ("schools", school.id),
                 ("required_room_tags", tag.id), ("email", "judge@example.test"), ("reported_experience", 6)]
        assert post("/enter_judge/", [*judge, ("request_wing", "on"), ("no_wings", "on")])[0] == 422
        assert post("/enter_judge/", [*judge, ("request_wing", "on")])[0] == 302
        judge_obj = Judge.objects.get(name="Roster Judge")
        assert judge_obj.rank == Decimal("5.50")
        assert judge_obj.request_wing and judge_obj.schools.get() == school
        assert judge_obj.ballot_code and judge_obj.required_room_tags.get() == tag
        judge_path = f"/judge/{judge_obj.id}/"
        assert post(judge_path, [*judge, ("checkin_0", "on")])[0] == 302
        assert CheckIn.objects.filter(judge=judge_obj, round_number=1).exists()
        assert post(judge_path, [*judge, ("checkin_1", "on")], session=helper)[0] == 302
        assert list(CheckIn.objects.filter(judge=judge_obj).values_list("round_number", flat=True)) == [1]

        room = [("name", "Roster Room"), ("rank", "4.25"), ("tags", tag.id)]
        assert post("/enter_room/", room)[0] == 302
        room_obj = Room.objects.get(name="Roster Room")
        assert room_obj.tags.get() == tag and room_obj.rank == Decimal("4.25")
        room_path = f"/room/{room_obj.id}/"
        assert post(room_path, [*room, ("checkin_-1", "on")])[0] == 302
        assert RoomCheckIn.objects.filter(room=room_obj, round_number=0).exists()
        opponent = Team.objects.create(name="Roster Opponent", school=school, seed=0)
        Round.objects.create(round_number=2, gov_team=team, opp_team=opponent, room=room_obj, victor=Round.GOV)
        assert post(f"{room_path}delete/", [])[0] == 422
        assert request(port, "/view_judges/", cookie=board)[1]["Location"] == "/403/"
        assert b"Roster Judge" in request(port, "/view_judges/", cookie=tab)[2]
        assert request(port, f"/judge/{judge_obj.id}/delete/", cookie=tab)[0] == 200
        assert Judge.objects.filter(pk=judge_obj.pk).exists()
        assert post(f"/judge/{judge_obj.id}/delete/", [])[0] == 302
        assert not Judge.objects.filter(pk=judge_obj.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_native_scratch_roles_keep_submission_blind_and_board_excluded():
    tab = legacy_tab_session("native-scratch-tab")
    board = legacy_tab_session("native-scratch-board")
    get_user_model().objects.get(username="native-scratch-board").groups.add(Group.objects.create(name="APDA Board"))

    def scoped_login(username, group):
        user = get_user_model().objects.create_user(username=username, is_staff=True)
        user.groups.add(Group.objects.create(name=group))
        client = Client()
        client.force_login(user)
        return client.cookies["sessionid"].value

    submitter = scoped_login("scratch-submitter", "MIT-TAB: Add scratches")
    reviewer = scoped_login("scratch-reviewer", "MIT-TAB: Create and view scratches")
    school = School.objects.create(name="Scratch School")
    team = Team.objects.create(name="Scratch Team", school=school, seed=0)
    opponent = Team.objects.create(name="Scratch Opponent", school=school, seed=0)
    judge = Judge.objects.create(name="Scratch Example Judge", rank=5)
    other_judge = Judge.objects.create(name="Second Example Judge", rank=4)
    TabSettings.set("cur_round", 1)
    TabSettings.set("tot_rounds", 1)
    Round.objects.create(round_number=1, gov_team=team, opp_team=opponent, victor=Round.GOV)

    start = "/enter_scratch/"
    global_list = "/scratches/view/"
    edit = f"/team/{team.id}/scratches/view/"
    with rust_server() as port:
        status, headers, body = request(port, start, cookie=submitter)
        assert status == 200 and b"Scratch Example Judge" in body
        csrf = load_cookies(headers)["csrftoken"].value

        def post(path, fields, session=tab):
            return request(port, path, method="POST",
                           extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": csrf, **fields})

        payload = {"team": team.id, "judge": judge.id, "scratch_type": Scratch.TAB_SCRATCH}
        first = post(start, payload, submitter)
        second = post(start, payload, submitter)
        assert first[0] == second[0] == 302
        assert first[1]["Location"] == second[1]["Location"] == f"{start}?submitted=1"
        assert first[2] == second[2]
        assert request(port, start, cookie=submitter)[2] != b""
        scratch = Scratch.objects.get()
        assert scratch.created_by.username == "scratch-submitter"
        assert AuditEvent.objects.filter(content_type__model="scratch", object_id=scratch.id).count() == 1
        for path in (start, global_list, edit, f"/judge/{judge.id}/scratches/view/"):
            assert request(port, path, cookie=board)[1]["Location"] == "/403/"
            assert post(path, payload, board)[1]["Location"] == "/403/"
        assert request(port, global_list, cookie=submitter)[1]["Location"] == "/403/"
        reviewer_response = request(port, edit, cookie=reviewer)
        assert reviewer_response[0] == 200 and b"Scratch Example Judge" in reviewer_response[2]
        assert b"Delete Scratch" not in reviewer_response[2] and b"Save Scratches" not in reviewer_response[2]

        changes = {"1-id": scratch.id, "1-team": team.id, "1-judge": other_judge.id,
                   "1-scratch_type": Scratch.TEAM_SCRATCH}
        assert post(edit, changes, reviewer)[1]["Location"] == "/403/"
        assert post(edit, changes)[1]["Location"] == edit
        scratch.refresh_from_db()
        assert scratch.judge_id == other_judge.id and scratch.scratch_type == Scratch.TEAM_SCRATCH
        assert scratch.created_by.username == "scratch-submitter"
        assert post(start, {**payload, "judge": 999999})[0] == 422

        add = f"/judge/{judge.id}/scratches/add/2/"
        invalid = {"1-team": team.id, "1-scratch_type": 0, "2-team": 999999, "2-scratch_type": 0}
        assert post(add, invalid, reviewer)[0] == 422
        assert Scratch.objects.count() == 1
        assert post(add, {**invalid, "2-team": opponent.id}, reviewer)[0] == 302
        assert Scratch.objects.count() == 3
        delete = f"/team/{team.id}/scratches/delete/{scratch.id}/"
        assert post(delete, {}, reviewer)[1]["Location"] == "/403/"
        assert request(port, delete, cookie=tab)[0] == 200
        assert Scratch.objects.filter(id=scratch.id).exists()
        assert post(delete, {})[1]["Location"] == edit
        assert not Scratch.objects.filter(id=scratch.id).exists()


@pytest.mark.django_db(transaction=True)
def test_native_roster_spreadsheets_roundtrip_and_atomically_reject_bad_rows():
    from django.core.files.uploadedfile import SimpleUploadedFile
    from mittab.libs.spreadsheets import read_rows, spreadsheet_response

    tab = legacy_tab_session("sheet-tab")
    board = legacy_tab_session("sheet-board")
    get_user_model().objects.get(username="sheet-board").groups.add(Group.objects.create(name="APDA Board"))
    helper = get_user_model().objects.create_user(username="sheet-helper", is_staff=True)
    helper.groups.add(Group.objects.create(name="MIT-TAB: Data entry helper"))
    client = Client()
    client.force_login(helper)
    entry = client.cookies["sessionid"].value
    TabSettings.set("cur_round", 1)
    TabSettings.set("tot_rounds", 2)
    path = "/import_data/"

    def workbook(fmt, header, rows):
        return bytes(spreadsheet_response("roster", fmt, header, rows).content)

    with rust_server() as port:
        status, headers, body = request(port, path, cookie=tab)
        assert status == 200 and b"Scratches" in body
        assert b"Scratches" not in request(port, path, cookie=entry)[2]
        assert request(port, path, cookie=board)[0] == 403
        csrf = load_cookies(headers)["csrftoken"].value

        def upload(kind, fmt, header, rows, session=tab, token=csrf):
            return multipart_request(port, path, fields={"csrfmiddlewaretoken": token},
                                     file=(f"roster.{fmt}", workbook(fmt, header, rows)),
                                     file_field=kind, cookie=f"sessionid={session}; csrftoken={csrf}")

        team_header = ["Debater 2 name", "School", "Team name", "Seed", "Debater 1 name",
                       "Debater 2 status", "Debater 2 email", "Debater 1 APDA ID", "Hybrid school"]
        teams = [("Debater Two", "Example School", "Imported Team", "half", "Debater One",
                  "Novice", "speaker@example.test", 71, "Example Hybrid")]
        assert upload("team_file", "xlsx", team_header, teams, session=entry, token="bad")[0] == 403
        mixed = upload("team_file", "xlsx", team_header,
                       [*teams, ("Debater Four", "Rollback School", "Conflicting Team", "invalid",
                                 "Debater Three", "Varsity", "", "", "")], session=entry)
        assert mixed[0] == 422 and b"Row 3" in mixed[2], mixed
        assert not Team.objects.exists() and not School.objects.exists() and not Debater.objects.exists()
        uploaded = upload("team_file", "xlsx", team_header, teams, session=entry)
        assert uploaded[0] == 302, uploaded
        team = Team.objects.get()
        assert team.name == "Imported Team" and team.seed == 2 and team.hybrid_school.name == "Example Hybrid"
        assert team.debaters.get(name="Debater One").apda_id == 71
        assert team.debaters.get(name="Debater Two").email == "speaker@example.test"
        assert team.created_by.username == "sheet-helper" and team.checked_in and team.ranking_public

        judge = upload("judge_file", "xls", ["Round 2 expected", "Judge name", "School 1", "Calibration"],
                       [("yes", "Imported Judge", "Example School", 5.25)])
        assert judge[0] == 302, judge
        judge = Judge.objects.get()
        assert judge.rank == Decimal("5.25") and judge.schools.get().name == "Example School"
        assert list(JudgeExpectedCheckIn.objects.filter(judge=judge).values_list("round_number", flat=True)) == [2]

        rooms = upload("room_file", "csv", ["Room name", "Rank"], [("Test Room", "3.5"), ("Invalid Room", "bad")])
        assert rooms[0] == 422 and b"Row 3" in rooms[2]
        assert not Room.objects.exists()
        rooms = upload("room_file", "csv", ["Room name", "Rank"], [("Test Room", "3.5")])
        assert rooms[0] == 302 and Room.objects.get().rank == Decimal("3.50")

        scratch_args = ("scratch_file", "csv", ["Judge name", "Scratch type", "Team name"],
                        [(judge.name, "tab", team.name)])
        assert upload(*scratch_args, session=entry)[0] == 422
        assert not Scratch.objects.exists()
        assert upload(*scratch_args)[0] == 302 and Scratch.objects.get().scratch_type == Scratch.TAB_SCRATCH
        assert request(port, "/export_data/scratches.csv", cookie=entry)[1]["Location"] == "/403/"
        for kind in ("teams", "judges", "rooms", "scratches"):
            for fmt in ("csv", "xls", "xlsx"):
                filename = f"{kind}.{fmt}"
                status, headers, data = request(port, f"/export_data/{filename}", cookie=tab)
                assert status == 200 and filename in headers["content-disposition"]
                values = read_rows(SimpleUploadedFile(filename, data))
                assert len(values) == 2
                _, exported = values[1]
                if kind == "teams":
                    assert exported[:4] == ["Imported Team", "Example School", "Example Hybrid", "Half Seed"]
                if kind == "judges":
                    assert exported[-2:] == ["", "yes"]
                if kind == "scratches":
                    assert exported == [team.name, judge.name, "tab"]


@pytest.mark.django_db(transaction=True)
def test_native_manual_pairing_corrections_are_transactional_and_staff_only():
    from mittab.apps.tab.models import ManualJudgeAssignment, NoShow

    tab = legacy_tab_session("manual-pairing-tab")
    board = legacy_tab_session("manual-pairing-board")
    get_user_model().objects.get(username="manual-pairing-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    TabSettings.set("allow_rejudges", 0)
    school = School.objects.create(name="Manual Pair School")
    gov = Team.objects.create(school=school, name="Government Before", seed=0)
    opp = Team.objects.create(school=school, name="Opposition Before", seed=0)
    spare = Team.objects.create(school=school, name="Replacement Team", seed=0)
    judge = Judge.objects.create(name="Original Chair", rank=8)
    wing = Judge.objects.create(name="Original Wing", rank=3)
    substitute = Judge.objects.create(name="Available Replacement", rank=5, wing_only=True)
    scratched = Judge.objects.create(name="Blocked judge", rank=99)
    room = Room.objects.create(name="Old Room", rank=1)
    other = Room.objects.create(name="Room With Tag", rank=4)
    unsuitable = Room.objects.create(name="Room Missing Tag", rank=3)
    tag = RoomTag.objects.create(tag="Required Projector", priority=1)
    gov.required_room_tags.add(tag)
    other.tags.add(tag)
    for person in (judge, wing, substitute, scratched):
        CheckIn.objects.create(judge=person, round_number=1)
    for location in (room, other, unsuitable):
        RoomCheckIn.objects.create(room=location, round_number=1)
    Scratch.objects.create(team=gov, judge=scratched, scratch_type=Scratch.TAB_SCRATCH)
    debate = Round.objects.create(round_number=1, gov_team=gov, opp_team=opp, chair=judge, room=room,
                                  pullup=Round.GOV, victor=Round.GOV)
    debate.judges.set([judge, wing])
    left = Outround.objects.create(num_teams=2, type_of_round=Outround.VARSITY, gov_team=gov, opp_team=opp,
                                   chair=judge, room=room, choice=Outround.GOV)
    left.judges.set([judge, wing])
    BreakingTeam.objects.create(team=gov, seed=1, effective_seed=1, type_of_team=BreakingTeam.VARSITY)
    BreakingTeam.objects.create(team=opp, seed=2, effective_seed=2, type_of_team=BreakingTeam.VARSITY)
    NoShow.objects.create(no_show_team=spare, round_number=1)
    path = f"/round/{debate.id}/edit/"

    with rust_server() as port:
        status, headers, page = request(port, path, cookie=tab)
        assert status == 200, (status, page)
        assert b"Available Replacement" in page and b"Wing only" in page
        assert b"Blocked judge" not in page
        assert b"Room With Tag" in page and b"Room Missing Tag" not in page
        csrf = load_cookies(headers)["csrftoken"].value
        revision = re.search(rb'name="revision" value="([^"]+)"', page).group(1).decode()

        def post(path, data=None, *, session=tab, token=csrf):
            return request(port, path, method="POST", extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": token, **(data or {})})

        commands = [
            f"/round/{debate.id}/assign_room/{other.id}/",
            f"/round/{debate.id}/assign_judge/{substitute.id}/{judge.id}/",
            f"/round/{debate.id}/assign_chair/{wing.id}/",
            f"/round/{debate.id}/remove_judge/{substitute.id}/",
            f"/round/{debate.id}/switch_sides/",
            f"/pairings/assign_team/{debate.id}/gov/{spare.id}/",
            f"/outround/{left.id}/assign_room/{other.id}/",
            f"/outround/{left.id}/assign_judge/{substitute.id}/",
            f"/outround/{left.id}/switch_sides/",
        ]
        assert request(port, path, cookie=board)[1]["Location"] == "/403/"
        assert request(port, f"/round/{debate.id}/alternative_judges/", cookie=board)[1]["Location"] == "/403/"
        for command in commands:
            assert post(command, session=board)[1]["Location"] == "/403/"
            assert post(command, token="invalid")[0] == 403
        assert debate.judges.count() == 2 and left.judges.count() == 2
        assert post(commands[0], {"revision": revision})[0] == 200
        assert post(commands[1], {"revision": revision})[0] == 422
        assert post(f"/round/{debate.id}/assign_judge/{substitute.id}/{scratched.id}/")[0] == 422
        assert set(debate.judges.values_list("id", flat=True)) == {judge.id, wing.id}
        result = post(commands[1])
        assert result[0] == 200 and json.loads(result[2])["chair_id"] == substitute.id
        assert ManualJudgeAssignment.objects.filter(round=debate, judge=substitute).exists()
        assert post(commands[2])[0] == 200
        assert post(commands[3])[0] == 200
        assert not ManualJudgeAssignment.objects.filter(round=debate, judge=substitute).exists()
        assert post(commands[4])[0] == 200
        debate.refresh_from_db()
        assert (debate.gov_team_id, debate.opp_team_id, debate.pullup, debate.room_id, debate.chair_id) == (
            opp.id, gov.id, Round.OPP, other.id, wing.id,
        )
        assert post(f"/pairings/assign_team/{debate.id}/gov/{gov.id}/")[0] == 422
        assert post(commands[5])[0] == 200
        assert not NoShow.objects.filter(no_show_team=spare).exists()

        for command in commands[6:]:
            assert post(command)[0] == 200
        left.refresh_from_db()
        assert (left.gov_team_id, left.opp_team_id, left.choice, left.room_id, left.chair_id) == (
            opp.id, gov.id, Outround.OPP, other.id, judge.id,
        )
        assert set(left.judges.values_list("id", flat=True)) == {judge.id, wing.id, substitute.id}
        manual = AuditEvent.objects.filter(event_type__startswith="manual_")
        for event in manual:
            assert "rank" not in event.changes and "wing_only" not in event.changes
        board_page = request(port, "/apda-board/inrounds/1/", cookie=board)[2]
        assert b"Blocked judge" not in board_page and b"Wing only" not in board_page and b"Requests no wings" not in board_page


@pytest.mark.django_db(transaction=True)
def test_native_staff_pairing_stats_checkin_and_csv():
    from mittab.libs.data_export.pairings_export import export_pairings_csv

    tab = legacy_tab_session("round-tools-tab")
    school = School.objects.create(name="Round Tools School")
    gov = Team.objects.create(name="Government Export", seed=3, school=school)
    opp = Team.objects.create(name="Opposition Export", seed=0, school=school)
    bye = Team.objects.create(name="Export Bye", seed=0, school=school)
    Team.objects.create(name="Export Unpaired", seed=0, school=school)
    gov_speaker = Debater.objects.create(name="Export Gov Debater", novice_status=0)
    opp_speaker = Debater.objects.create(name="Export Opp Debater", novice_status=1)
    gov.debaters.add(gov_speaker)
    opp.debaters.add(opp_speaker)
    judge = Judge.objects.create(name="Export Judge", rank="8.25")
    room = Room.objects.create(name="Export Room", rank="4.50")
    debate = Round.objects.create(round_number=1, gov_team=gov, opp_team=opp, chair=judge,
                                  room=room, victor=Round.GOV)
    debate.judges.add(judge)
    Bye.objects.create(round_number=1, bye_team=bye)
    RoundStats.objects.create(round=debate, debater=gov_speaker, speaks=Decimal("28.0"), ranks=1, debater_role="PM")
    RoundStats.objects.create(round=debate, debater=opp_speaker, speaks=Decimal("27.5"), ranks=2, debater_role="LO")
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 5)
    TabSettings.set("lenient_late", 0)

    with rust_server() as port:
        checkin = "/pairings/quick_judge_checkin/"
        status, headers, page = request(port, checkin, cookie=tab)
        assert status == 200 and b"Export Judge" in page
        csrf = load_cookies(headers)["csrftoken"].value

        def post(fields):
            return request(port, checkin, method="POST", extra_headers={"Cookie": f"sessionid={tab}; csrftoken={csrf}"},
                           form=[("csrfmiddlewaretoken", csrf), *fields])

        assert post([("round_number", 6), ("judge_id", judge.id)])[0] == 400
        assert post([("round_number", 2), ("judge_id", 999999)])[0] == 404
        present = post([("round_number", 2), ("judge_id", judge.id)])
        assert json.loads(present[2]) == {"success": True, "created": False,
                                         "judge": {"id": judge.id, "name": judge.name, "rank": "8.25"}}
        assert post([("round_number", 2), ("judge_id", judge.id)])[0] == 200
        assert CheckIn.objects.filter(judge=judge, round_number=2).count() == 1
        created = post([("round_number", 2), ("name", "New Walk-In Judge"), ("school_ids[]", school.id)])
        assert created[0] == 200 and json.loads(created[2])["created"]
        walk_in = Judge.objects.get(name="New Walk-In Judge")
        assert walk_in.rank == 5 and walk_in.schools.get() == school
        assert CheckIn.objects.filter(judge=walk_in, round_number=2).exists()

        status, headers, raw = request(port, "/pairings/export_csv/", cookie=tab)
        assert status == 200 and headers["Content-Disposition"].endswith("round_1_pairings.csv")
        assert raw == export_pairings_csv().content
        outcome = request(port, "/round/1/stats/", cookie=tab)
        assert outcome[0] == 200
        stats = json.loads(outcome[2])
        assert stats[str(gov.id)]["wins"] == 1 and stats[str(gov.id)]["seed"] == "Full"
        assert stats[str(gov.id)]["total_speaks"] == 28.0
        assert stats[str(opp.id)]["opps"] == 1 and stats[str(opp.id)]["debaters"] == opp_speaker.name
        stats_page = request(port, "/pairings/round_stats/", cookie=tab)[2]
        assert b"All preliminary rounds" in stats_page and b"27.75" in stats_page
        assert b"Round 5" in request(port, "/pairings/view_rounds/", cookie=tab)[2]


@pytest.mark.django_db(transaction=True)
def test_native_staff_standard_break_panels_publication_and_next_stage(tmp_path):
    tab = legacy_tab_session("elimination-staff")
    board = legacy_tab_session("elimination-board")
    get_user_model().objects.get(username="elimination-board").groups.add(Group.objects.create(name="APDA Board"))
    for key, value in {
        "cur_round": 2, "tot_rounds": 1, "var_teams_to_break": 4, "nov_teams_to_break": 2,
        "outround_pairing_mode": 0, "var_panel_size": 2, "nov_panel_size": 1, "outround_judge_priority": 1,
        "enable_room_seeding": 1, "var_teams_visible": 256, "nov_teams_visible": 256,
    }.items():
        TabSettings.set(key, value)
    school = School.objects.create(name="Elim Test School")
    teams = []
    for index in range(6):
        novice = int(index >= 4)
        team = Team.objects.create(name=f"Elim Team {index + 1}", school=school, seed=0, break_preference=novice)
        team.debaters.add(Debater.objects.create(name=f"Elim Debater {index + 1}", novice_status=novice))
        teams.append(team)
    preliminary = Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1])
    Round.objects.create(round_number=1, gov_team=teams[2], opp_team=teams[3], victor=Round.OPP)
    Round.objects.create(round_number=1, gov_team=teams[4], opp_team=teams[5], victor=Round.GOV)
    for index in range(3):
        room = Room.objects.create(name=f"Elim Room {index + 1}", rank=index + 1)
        RoomCheckIn.objects.create(room=room, round_number=0)
    judges = []
    for index in range(6):
        judge = Judge.objects.create(name=f"Elim Judge {index + 1}", rank=index + 1)
        CheckIn.objects.create(judge=judge, round_number=0)
        judges.append(judge)
    Scratch.objects.create(team=teams[0], judge=judges[-1], scratch_type=Scratch.TAB_SCRATCH)

    with rust_server(overrides={"BACKUP_PREFIX": str(tmp_path), "BACKUP_STORAGE": ""}) as port:
        start = "/break/"
        status, headers, body = request(port, start, cookie=tab)
        assert status == 200 and b"Enter all preliminary outcomes" in body
        csrf = load_cookies(headers)["csrftoken"].value

        def post(path, fields=None, session=tab):
            return request(port, path, method="POST",
                           extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": csrf, **(fields or {})})

        assert post(start)[0] == 422
        assert not BreakingTeam.objects.exists() and not list(tmp_path.glob("*.dump.sql"))
        Round.objects.filter(id=preliminary.id).update(victor=Round.GOV)
        result = post(start)
        assert result[0] == 302 and result[1]["Location"] == "/outround_pairing", result
        assert BreakingTeam.objects.count() == 6
        assert Outround.objects.filter(type_of_round=0, num_teams=4).count() == 2
        assert Outround.objects.filter(type_of_round=1, num_teams=2).count() == 1
        assert not Outround.objects.exclude(chair=None, room=None).exists()
        assert list(tmp_path.glob("Before the Break*.dump.sql"))
        assert post(start)[0] == 422
        scope = {"selected_varsity": 4, "selected_novice": 2}
        endpoint = "/outround_pairing/assign_judges/"
        assert post(endpoint, scope, session=board)[1]["Location"] == "/403/"
        assert request(port, "/outround_pairing/0/4?select_varsity=4&select_novice=", cookie=tab)[0] == 200

        assign_judges.add_outround_judges(round_specs=[(0, 4), (1, 2)])
        expected = {row.id: (row.chair_id, set(row.judges.values_list("id", flat=True))) for row in Outround.objects.all()}
        for row in Outround.objects.all():
            row.judges.clear()
        Outround.objects.all().update(chair=None)
        result = post(endpoint, scope)
        assert result[0] == 302, result
        actual = {row.id: (row.chair_id, set(row.judges.values_list("id", flat=True))) for row in Outround.objects.all()}
        assert actual == expected
        room_endpoint = "/outround_pairing/assign_rooms/"
        assert post(room_endpoint, scope)[0] == 302
        assert len(set(Outround.objects.values_list("room_id", flat=True))) == 3
        assert json.loads(post("/outround_pairing/release/4/0/")[2])["pairing_released"]
        assert TabSettings.get("var_teams_visible", use_cache=False) == 4
        status, _, csv_data = request(port, "/outround_pairings/export_csv/0/", cookie=tab)
        assert status == 200 and b"[V] Ro4," in csv_data
        rows = list(Outround.objects.filter(type_of_round=0).order_by("id"))
        hop = "/outround_pairing/pair/0/4/"
        assert post(hop)[0] == 422
        for row in rows:
            ballot = f"/outround/{row.id}/result/"
            assert request(port, ballot, cookie=tab)[0] == 200
            assert post(ballot, {"winner": 1, "round_instance": 9999})[0] == 422
            assert post(ballot, {"winner": 1, "round_instance": row.id})[0] == 302
        assert b"drops to" in request(port, "/outround_result/0", cookie=tab)[2]
        result = post(hop)
        assert result[0] == 302 and result[1]["Location"] == "/outround_pairing/0/2", result
        final = Outround.objects.get(type_of_round=0, num_teams=2)
        assert {final.gov_team_id, final.opp_team_id} == {row.gov_team_id for row in rows}
        assert not final.judges.exists() and final.chair_id is None
        assert set(Outround.objects.filter(type_of_round=0, num_teams=4).values_list("id", flat=True)) == {row.id for row in rows}
        assert TabSettings.get("var_teams_visible", use_cache=False) == 4


@pytest.mark.django_db(transaction=True)
def test_native_elimination_challenge_once_enforces_pool_and_seeds(tmp_path):
    from mittab.apps.tab.models import OutroundChallenge

    tab = legacy_tab_session("challenge-staff")
    for key, value in {
        "cur_round": 2, "tot_rounds": 1, "var_teams_to_break": 4, "nov_teams_to_break": 0,
        "outround_pairing_mode": 2, "var_panel_size": 1,
    }.items():
        TabSettings.set(key, value)
    school = School.objects.create(name="Challenge School")
    teams = [Team.objects.create(name=f"Challenge Team {index + 1}", school=school, seed=0) for index in range(4)]
    for index in range(2):
        room = Room.objects.create(name=f"Challenge Room {index + 1}", rank=index + 1)
        RoomCheckIn.objects.create(room=room, round_number=0)
        Round.objects.create(round_number=1, gov_team=teams[index * 2], opp_team=teams[index * 2 + 1], victor=Round.GOV)
    with rust_server(overrides={"BACKUP_PREFIX": str(tmp_path), "BACKUP_STORAGE": ""}) as port:
        status, headers, _ = request(port, "/break/", cookie=tab)
        assert status == 200
        csrf = load_cookies(headers)["csrftoken"].value

        def post(path, fields=None):
            return request(port, path, method="POST",
                           extra_headers={"Cookie": f"sessionid={tab}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": csrf, **(fields or {})})

        assert post("/break/")[0] == 302
        assert BreakingTeam.objects.filter(type_of_team=0).count() == 4 and not Outround.objects.exists()
        assert b"Prepare Challenges" in request(port, "/outround_pairing/0/4", cookie=tab)[2]
        path = "/outround_pairing/pair/0/8/"
        seeds = list(BreakingTeam.objects.order_by("seed"))
        a, b, c, d = [seed.team_id for seed in seeds]
        assert b"Challenge Picks" in request(port, path, cookie=tab)[2]
        action = {"action": "set_challenge", "challenger_id": a, "challenged_id": c}
        assert post(path, {**action, "challenged_id": a})[0] == 422
        assert post(path, action)[0] == 302
        assert post(path, {**action, "challenger_id": b})[0] == 422
        assert OutroundChallenge.objects.count() == 1
        assert post(path, {"action": "delete_challenge", "challenger_id": a})[0] == 302
        assert not OutroundChallenge.objects.exists()
        assert post(path, action)[0] == 302
        result = post(path)
        assert result[0] == 302 and result[1]["Location"] == "/outround_pairing/0/4", result
        assert {frozenset((row.gov_team_id, row.opp_team_id)) for row in Outround.objects.all()} == {
            frozenset((a, c)), frozenset((b, d)),
        }
        assert BreakingTeam.objects.get(team_id=c).effective_seed == 4
        assert TabSettings.get(f"challenge_once_applied_0_{min(seed.id for seed in seeds)}", use_cache=False) == 1
        assert post(path, action)[0] == 422
