"""Compare the Rust HTTP server with Django over a shared disposable test DB."""

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
from pathlib import Path
import subprocess
from urllib.parse import urlencode

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import caches
from django.db import connection
from django.test import Client
from django.utils import timezone

from mittab.apps.registration.models import InfoLink, InfoText, RegistrationConfig

from mittab.apps.tab.models import (
    BreakingTeam, Bye, Debater, Judge, JudgeExpectedCheckIn, Motion, Outround, PublishedTeamPlacement, Round, RoundStats,
    PublicRosterChange, Room, School, Scratch, TabSettings, Team,
)

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
def rust_server(session_key_configured=True):
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
