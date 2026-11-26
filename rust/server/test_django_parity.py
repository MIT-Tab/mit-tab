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
def isolate_tournament_settings():
    caches["default"].clear()
    caches["filesystem"].clear()
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
