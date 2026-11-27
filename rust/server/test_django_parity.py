"""Compare the Rust HTTP server with Django over a shared disposable test DB."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from difflib import unified_diff
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
import sys
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import caches
from django.core.management import call_command
from django.db import connection, transaction
from django.test import Client, override_settings
from django.utils import timezone
from webpack_loader import utils as webpack_utils

from mittab.apps.registration.models import InfoLink, InfoText, RegistrationConfig

from mittab.apps.tab.models import (
    AuditEvent, BreakingTeam, Bye, CheckIn, Debater, Judge, JudgeExpectedCheckIn, Motion, Outround, PublishedTeamPlacement, Round, RoundStats,
    NoShow, PublicDisplaySetting, PublicRosterChange, Room, RoomCheckIn, RoomTag, School, Scratch, TabSettings, Team,
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
        "SENTRY_DSN": "",
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
            diagnostics = process.stderr.read()
            if diagnostics:
                print(diagnostics)


def request(port, path, *, method="GET", cookie=None, forwarded_proto=None, form=None,
            extra_headers=None, raw_body=None):
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
                       body=raw_body if form is None else urlencode(form, doseq=True))
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


@pytest.fixture
def html_assets(tmp_path, monkeypatch):
    chunks = {
        bundle: [f"{bundle}.css", f"{bundle}-fixture.js"]
        for bundle in ("main", "publicDisplay", "publicHome", "batchCheckin", "bracket", "tournamentStart", "tournamentTodo")
    }
    assets = {
        name: {"name": name, "publicPath": f"/static/webpack_bundles/{name}"}
        for chunk in chunks.values()
        for name in chunk
    }
    manifest = tmp_path / "webpack-stats.json"
    manifest.write_text(json.dumps(dict(status="done", assets=assets, chunks=chunks)))
    webpack = {
        **webpack_utils.load_config("DEFAULT"),
        "STATS_FILE": manifest,
        "CACHE": False,
        "LOADER_CLASS": "webpack_loader.loaders.WebpackLoader",
    }
    monkeypatch.setattr(webpack_utils, "load_config", lambda _: webpack)
    webpack_utils.get_loader.cache_clear()
    yield {"MITTAB_WEBPACK_STATS": str(manifest)}
    webpack_utils.get_loader.cache_clear()


def assert_html_bytes(actual, expected):
    if actual != expected:
        diff = unified_diff(
            expected.decode().splitlines(keepends=True),
            actual.decode().splitlines(keepends=True),
            fromfile="Django",
            tofile="Rust",
            n=2,
        )
        pytest.fail("".join(diff), pytrace=False)


@pytest.mark.django_db(transaction=True)
def test_public_html_matches_django_bytes(html_assets):
    TabSettings.set("motions_enabled", 1)
    TabSettings.set("theme_color", "#F0C020")
    TabSettings.set("tournament_name", "Tournament 'A' & B / C")
    TabSettings.set("start_tournament_wizard_completed", 1)
    Motion.objects.create(
        round_number=1, info_slide="A < B & \"C\" / X\r\nZ",
        motion_text="This house prefers O’Connor's / example.\nSecond line.",
        is_published=True,
    )
    Motion.objects.create(round_number=2, motion_text="Private motion", is_published=False)
    InfoLink.objects.create(title="Example & resources", url="https://example.com/resources?a=x&b=y")
    InfoLink.objects.create(title="Internationalized resources", url="https://bücher.de/verzeichnis")
    InfoText.objects.create(title="Tournament notes", text="Bring A & B")
    TabSettings.set("tournament_director", "Alex Example\nPat Example")
    caches["public"].clear()
    user = get_user_model().objects.create_superuser(username="byte-parity")

    with rust_server(overrides=html_assets) as port:
        for authenticated in (False, True):
            client = Client()
            if authenticated:
                client.force_login(user)
            session = client.cookies.get("sessionid")
            for path in ("/public/", "/public/access-error/", "/public/motions/"):
                response = client.get(path)
                status, _, body = request(port, path, cookie=session.value if session else None)
                assert status == response.status_code == 200
                assert_html_bytes(body, response.content)


@pytest.mark.django_db(transaction=True)
def test_staff_login_html_matches_django_bytes(html_assets):
    path = "/public/login/?next=%2Fpublic%2F%3Fa%3D1%26b%3D2"
    with rust_server(overrides=html_assets) as port:
        status, headers, body = request(port, path)
    jar = SimpleCookie(headers["Set-Cookie"])
    token = re.search(rb'name="csrfmiddlewaretoken" value="([A-Za-z0-9]+)"', body)[1].decode()
    assert len(token) == 64
    client = Client()
    client.cookies["csrftoken"] = jar["csrftoken"].value
    with patch("django.middleware.csrf._get_new_csrf_string", return_value=token[:32]):
        response = client.get(path)
    assert status == response.status_code == 200
    assert_html_bytes(body, response.content)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("include_speaks", [False, True])
def test_public_rankings_html_matches_django_bytes(html_assets, include_speaks):
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 2)
    TabSettings.set("start_tournament_wizard_completed", 1)
    school = School.objects.create(name="Example School")
    first = Team.objects.create(name="Alpha & Beta", school=school, seed=Team.UNSEEDED)
    second = Team.objects.create(name="O’Connor's", school=school, seed=Team.UNSEEDED)
    debater = Debater.objects.create(name="One < Two", novice_status=0)
    first.debaters.add(debater)
    debate = Round.objects.create(round_number=1, gov_team=first, opp_team=second, victor=Round.GOV)
    RoundStats.objects.create(round=debate, debater=debater, speaks=Decimal("31.7450"), ranks=1)
    for slug, label in (("team", "Team"), ("varsity", "Varsity Speakers"), ("novice", "Novice Speakers")):
        PublicDisplaySetting.objects.create(
            slug=slug, label=label, display_type=PublicDisplaySetting.RANKING,
            is_enabled=True, include_speaks=include_speaks, round_number=1, max_visible=20,
        )
    PublicDisplaySetting.objects.create(
        slug="ballot_round_1", label="Round 1", display_type=PublicDisplaySetting.BALLOT,
        is_enabled=True, include_speaks=include_speaks, include_ranks=True, round_number=1,
    )
    user = get_user_model().objects.create_superuser(username="rankings-parity")
    caches["public"].clear()
    with rust_server(overrides=html_assets) as port:
        for authenticated in (False, True):
            client = Client()
            if authenticated:
                client.force_login(user)
            session = client.cookies.get("sessionid")
            for path in ("/public/team-rankings/", "/public/speaker-rankings/", "/public/ballots/"):
                response = client.get(path)
                status, _, body = request(port, path, cookie=session.value if session else None)
                assert status == response.status_code == 200
                assert_html_bytes(body, response.content)

        status, headers, _ = request(port, f"/team/{first.id}", cookie=session.value)
        assert status == 302 and headers["Location"] == f"/team/{first.id}/"


@pytest.mark.django_db(transaction=True)
def test_backup_listing_html_matches_django_bytes(html_assets, tmp_path):
    from mittab.libs.backup import get_metadata

    TabSettings.set("start_tournament_wizard_completed", 1)
    user = get_user_model().objects.create_superuser(username="backup-list-parity")
    client = Client()
    client.force_login(user)
    session = client.cookies["sessionid"].value
    key = "Example & copy_0_2_1700000000_0"
    (tmp_path / f"{key}.dump.sql").touch()
    overrides = {**html_assets, "BACKUP_PREFIX": str(tmp_path), "BACKUP_STORAGE": ""}
    path = "/pairing/view_backups/"
    with rust_server(overrides=overrides) as port:
        status, headers, body = request(port, path, cookie=session)
    assert status == 200
    jar = SimpleCookie(headers["Set-Cookie"])
    token = re.search(rb'name="csrfmiddlewaretoken" value="([A-Za-z0-9]+)"', body)[1].decode()
    client.cookies["csrftoken"] = jar["csrftoken"].value
    with patch("django.middleware.csrf._get_new_csrf_string", return_value=token[:32]), \
         patch("mittab.libs.backup.list_backups", return_value=[get_metadata(key)]):
        response = client.get(path)
    assert response.status_code == 200
    assert_html_bytes(body, response.content)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "screen",
    [
        "pairings",
        "pairings previous",
        "pairings empty",
        "pairings prepare",
        "pairings prepare pending",
        "pairings repair",
        "pairings team choices",
        "pairings room choices",
        "pairings judge choices",
        "admin index",
        "admin app",
        "scores",
        "empty scores",
        "scores second page",
        "score repair",
        "add score",
        "score delete",
        "score history",
        "delete selected scores",
    ],
)
def test_staff_screenshot_html_matches_django_bytes(html_assets, screen):
    TabSettings.set("start_tournament_wizard_completed", 1)
    TabSettings.set("cur_round", 2)
    school = School.objects.create(name="Example Debate Club")
    first = Team.objects.create(name="North Debate", school=school, seed=Team.UNSEEDED)
    second = Team.objects.create(name="South Debate", school=school, seed=Team.UNSEEDED)
    speaker = Debater.objects.create(name="Example Speaker", novice_status=0)
    first.debaters.add(speaker)
    debate = Round.objects.create(round_number=1, gov_team=first, opp_team=second, victor=Round.GOV)
    score = RoundStats.objects.create(round=debate, debater=speaker, speaks=Decimal("29.25"), ranks=1)
    user = get_user_model().objects.create_superuser(
        username="staff-screenshot-parity", first_name="Fixture & Staff",
    )
    if screen.startswith("pairings") and screen != "pairings empty":
        chair = Judge.objects.create(name="Chair & Example", rank=Decimal("6.25"), request_wing=True)
        wing = Judge.objects.create(name="A Wing", rank=Decimal("4.35"), wing_only=True)
        checked = Judge.objects.create(name="Checked in judge", rank=Decimal("5.00"))
        Judge.objects.create(name="Absent judge", rank=Decimal("7.20"))
        chair_room = Room.objects.create(name="Room & Example", rank=8)
        Room.objects.create(name="Unused room", rank=3)
        tag = RoomTag.objects.create(tag="Accessible", priority=1)
        first.required_room_tags.add(tag)
        debate.chair = chair
        debate.room = chair_room
        debate.save(update_fields=["chair", "room"])
        debate.judges.add(chair, wing)
        CheckIn.objects.create(judge=checked, round_number=1)
        if "choices" in screen:
            tagged_room = Room.objects.create(name="Accessible room", rank=7)
            tagged_room.tags.add(tag)
            RoomCheckIn.objects.create(room=tagged_room, round_number=1)
        if screen == "pairings prepare pending":
            debate.victor = Round.NONE
            debate.save(update_fields=["victor"])
            checked.schools.add(school)
            CheckIn.objects.create(judge=checked, round_number=2)
        from mittab.apps.tab.models import ManualJudgeAssignment
        ManualJudgeAssignment.objects.update_or_create(round=debate, judge=wing, defaults={"created_by": user})
        AuditEvent.record(debate, AuditEvent.MANUAL_JUDGE_ASSIGN, user, note="Assigned by hand")
        AuditEvent.record(debate, AuditEvent.EDIT, user, changes={"public": {
            "source": "staff team correction",
            "before": {"gov_team": {"id": second.id, "name": second.name}},
            "after": {"gov_team": {"id": first.id, "name": first.name}},
        }})
        spare = Team.objects.create(name="Spare team", school=school, seed=Team.UNSEEDED, checked_in=True)
        if screen == "pairings previous":
            TabSettings.set("cur_round", 3)
            TabSettings.set("pairing_released", 1)
            previous = Round.objects.create(round_number=2, gov_team=first, opp_team=spare)
            previous.judges.add(chair)
            Bye.objects.create(bye_team=spare, round_number=1)
    elif screen == "pairings empty":
        debate.delete()
        TabSettings.set("cur_round", 1)
    if screen == "empty scores":
        score.delete()
    elif screen == "scores second page":
        RoundStats.objects.bulk_create([
            RoundStats(round=debate, debater=speaker, speaks=score.speaks, ranks=1)
            for _ in range(100)
        ])
    if screen == "score history":
        from django.contrib.admin.models import CHANGE, LogEntry
        from django.contrib.contenttypes.models import ContentType

        LogEntry.objects.create(
            action_time=timezone.make_aware(datetime(2025, 11, 12, 14, 5)),
            content_type=ContentType.objects.get_for_model(RoundStats),
            user=user, object_id=score.id, object_repr=str(score),
            action_flag=CHANGE, change_message='[{"changed": {"fields": ["Speaks"]}}]',
        )
    client = Client()
    client.force_login(user)
    session = client.cookies["sessionid"].value
    csrf = "a" * 32
    client.cookies["csrftoken"] = csrf
    path = {
        "pairings": "/pairings/status/",
        "pairings previous": "/round/1/",
        "pairings empty": "/pairings/status/",
        "pairings prepare": "/pairing/pair_round/",
        "pairings prepare pending": "/pairing/pair_round/",
        "pairings repair": "/pairing/re_pair_round/",
        "pairings team choices": f"/round/{debate.id}/{first.id}/alternative_teams/gov/",
        "pairings room choices": f"/round/{debate.id}/alternative_rooms/{getattr(debate, 'room_id', None)}/",
        "pairings judge choices": f"/round/{debate.id}/alternative_judges/{getattr(debate, 'chair_id', None)}/",
        "admin index": "/admin/",
        "admin app": "/admin/tab/",
        "scores": "/admin/tab/roundstats/",
        "empty scores": "/admin/tab/roundstats/",
        "scores second page": "/admin/tab/roundstats/?p=2",
        "score repair": f"/admin/tab/roundstats/{score.id}/change/",
        "add score": "/admin/tab/roundstats/add/",
        "score delete": f"/admin/tab/roundstats/{score.id}/delete/",
        "score history": f"/admin/tab/roundstats/{score.id}/history/",
        "delete selected scores": "/admin/tab/roundstats/",
    }[screen]
    post_data = (
        {"action": "delete_selected", "_selected_action": score.id, "index": "0", "csrfmiddlewaretoken": csrf}
        if screen == "delete selected scores" else None
    )
    with rust_server(overrides=html_assets) as port:
        status, _, body = request(
            port, path, extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
            method="POST" if post_data else "GET", form=post_data,
        )
    token = re.search(rb'name="csrfmiddlewaretoken" value="([A-Za-z0-9]+)"', body)
    mask = token[1][:32].decode() if token and len(token[1]) == 64 else csrf
    with patch("django.middleware.csrf._get_new_csrf_string", return_value=mask):
        response = client.post(path, post_data) if post_data else client.get(path)
    assert status == response.status_code == 200
    if screen.startswith("pairings"):
        # The native form carries the displayed round to prevent stale-page writes.
        body = re.sub(rb'<input type="hidden" name="round" value="\d+">', b'', body)
    assert_html_bytes(body, response.content)


def test_admin_template_sources_match_locked_django():
    from django.template.loader import get_template

    vendor = Path(__file__).parent / "templates" / "django_admin"
    for snapshot in vendor.rglob("*.html"):
        template = get_template(f"admin/{snapshot.relative_to(vendor).as_posix()}")
        assert snapshot.read_bytes() == Path(template.origin.name).read_bytes(), snapshot


@pytest.mark.django_db(transaction=True)
def test_rust_serves_the_django_static_bytes(tmp_path):
    root = Path(__file__).resolve().parents[2]
    before, after = tmp_path / "django", tmp_path / "rust"
    with override_settings(STATIC_ROOT=before):
        call_command("collectstatic", interactive=False, verbosity=0)
    subprocess.run(
        [sys.executable, root / "bin" / "collect-static.py", root / "assets", after],
        check=True, capture_output=True,
    )
    paths = {path.relative_to(before) for path in before.rglob("*") if path.is_file()}
    assert paths == {path.relative_to(after) for path in after.rglob("*") if path.is_file()}
    assert Path("admin/js/jquery.init.js") in paths
    assert Path("admin/css/base.css") in paths
    assert Path("webpack_bundles/main.css") in paths
    manifest = json.loads((root / "webpack-stats.json").read_text())
    scripts = {
        Path("webpack_bundles") / name
        for chunk in manifest["chunks"].values()
        for name in chunk
        if name.endswith(".js")
    }
    assert scripts and scripts <= paths
    with rust_server(overrides={"MITTAB_STATIC_ROOT": str(after)}) as port:
        for path in sorted(paths):
            expected = (before / path).read_bytes()
            assert (after / path).read_bytes() == expected, path
            if path.suffix in {".css", ".js"}:
                status, _, body = request(port, f"/static/{path.as_posix()}")
                assert status == 200 and body == expected, path

        path = "/dynamic-media/jsi18n/"
        TabSettings.set("start_tournament_wizard_completed", 1)
        user = get_user_model().objects.create_superuser(username="catalog-parity")
        client = Client()
        client.force_login(user)
        reference = client.get(path)
        status, headers, body = request(port, path, cookie=client.cookies["sessionid"].value)
        assert status == reference.status_code == 200
        assert headers["Content-Type"] == reference["Content-Type"]
        assert body == reference.content


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
    visitor = Client().session
    visitor["_return_to"] = "/public/pairings/"
    visitor.save()
    with rust_server(session_key_configured=False) as port:
        assert request(port, "/public/", cookie=visitor.session_key)[0] == 200
        status, headers, _ = request(port, COUNTS, cookie=cookie)
        assert status == 303 and headers["Location"] == "/public/login/"
        assert headers["Cache-Control"] == "private, no-store"
        assert request(port, headers["Location"], cookie=cookie)[0] == 200
        assert request(port, COUNTS)[0] == 200
    with rust_server(overrides={"MITTAB_DJANGO_SECRET_KEY": "wrong-fixture-key"}) as port:
        status, headers, _ = request(port, COUNTS, cookie=cookie)
        assert status == 303 and headers["Location"] == "/public/login/"


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
        baseline = json.loads(request(port, LIVE, forwarded_proto="https")[2])
        assert baseline == json.loads(request(port, LIVE, forwarded_proto="https")[2])
        different_host = json.loads(request(port, LIVE, extra_headers={"Host": "fixture.example"})[2])
        assert different_host["source_url"] == "http://fixture.example"
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




@pytest.mark.django_db(transaction=True)
def test_live_publication_reads_after_concurrent_result_correction():
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    TabSettings.set("var_teams_visible", 2)
    school = School.objects.create(name="Publication fixture")
    first, second = [Team.objects.create(name=name, school=school, seed=0)
                     for name in ("Original winner", "Corrected winner")]
    final = Outround.objects.create(gov_team=first, opp_team=second, num_teams=2,
                                    type_of_round=0, victor=Outround.GOV)
    with rust_server() as port, ThreadPoolExecutor(max_workers=1) as pool:
        assert request(port, LIVE)[0] == 200
        with transaction.atomic():
            TabSettings.objects.select_for_update().get(key="cur_round")
            Outround.objects.filter(pk=final.pk).update(victor=Outround.OPP)
            # A public refresh must not establish its source snapshot until
            # the tournament mutation holding the round lock has committed.
            pending = pool.submit(request, port, LIVE)
            with pytest.raises(FutureTimeout):
                pending.result(timeout=0.8)
        status, _, body = pending.result()
        assert status == 200
        winner, = (row["team"]["tournament_id"] for row in json.loads(body)["team_results"]
                   if row["division"] == "varsity" and row["place"] == 1)
        assert winner == second.id
        assert PublishedTeamPlacement.objects.get(team=second, stage__num_teams=2).place == 1


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


def rust_command(command, database, *, tab_password=None, overrides=None, args=()):
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
        "TOURNAMENT_NAME": "",
        **(overrides or {}),
    }
    return subprocess.run(
        [binary, command, *args], env=environment, capture_output=True, text=True, timeout=30,
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
        "csrfmiddlewaretoken": (
            re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', body)[1].decode()
            if csrf_valid else "invalid-test-token"
        ),
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
        TabSettings.objects.filter(key="tot_rounds").delete()
        Outround.objects.create(gov_team=teams[0], opp_team=teams[1], num_teams=2, type_of_round=0)
        assert request(port, LIVE, cookie=session)[1]["Location"] == "/403/"
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
            if path != "/motions/add/":
                editing = path.endswith("/edit/")
                page = request(port, path if editing else "/motions/", extra_headers=jar)[2]
                body["motion_revision"] = staff_motion_proof(page, None if editing else path)
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
        assert b"Need 2, have 0" in body and b"Not enough judges" in body
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
        assert not Round.objects.filter(round_number=1, room__isnull=False).exists()
        assert request(port, status_page, cookie=tab)[2].count(b"Assign Room") >= 2

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
        status, _, body = request(
            port, release_page, method="POST",
            extra_headers={"Cookie": f"sessionid={tab}; csrftoken={csrf}",
                           "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest"},
            form={"round": 1},
        )
        assert status == 200 and json.loads(body) == {"pairing_released": False}
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
        page = request(port, "/pairings/status/", cookie=tab)[2]
        assert re.search(rb'judge-toggle[^"]*\bchair"[^>]*>\s*Assignment Judge', page)
        Judge.objects.filter(pk__in=[judge.id for judge in judges]).update(wing_only=True)
        status, _, body = assign()
        assert status == 409 and b"eligible chair" in body
        assert signature() == actual
        assert AuditEvent.objects.filter(event_type="judges_paired").count() == 12 * 4


@pytest.mark.django_db(transaction=True)
def test_native_single_and_panel_ballots_preserve_existing_results_on_invalid_input():
    from mittab.apps.tab.forms import ResultEntryForm, score_panel

    tab = legacy_tab_session("native-ballots")
    volunteer = get_user_model().objects.create_user(username="entry", password="fixture-entry")
    client = Client()
    client.force_login(volunteer)
    entry = client.cookies["sessionid"].value
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

        assert request(port, "/", cookie=entry)[1]["Location"] == "/pairings/status/"
        assert request(port, "/view_teams/", cookie=entry)[0] == 200
        assert request(port, "/enter_school/", cookie=entry)[0] == 200
        assert post("/enter_school/", {"name": "Volunteer School"}, session=entry)[0] == 302
        assert School.objects.filter(name="Volunteer School").exists()
        for denied in ("/settings_form", "/pairing/view_backups/", f"/team/{teams[0].id}/delete/"):
            assert request(port, denied, cookie=entry)[1]["Location"] == "/403/"
        assert request(port, single, cookie=entry)[0] == 200
        assert post(single, {**first, "rfd": " Synthetic note "}, session=entry)[1]["Location"] == "/pairings/status/"
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

        # The reference assigns distinct ranks before its tie-group pass.
        tied = [(Round.GOV, (30, 30, 28, 27), ranks) for ranks in ((1, 2, 3, 4), (2, 1, 3, 4))]
        grouped = {Round.GOV: [list(zip(speakers, roles, speaks, ranks)) for _, speaks, ranks in tied]}
        expected, _ = score_panel(grouped, discard_minority=False)
        tied_fields = {}
        for i, (winner, speaks, ranks) in enumerate(tied, 1):
            tied_fields.update(ballot(winner, speaks, ranks, f"{i}-"))
        assert post(f"{single}2/", tied_fields)[0] == 302
        assert dict(debate.roundstats_set.values_list("debater_role", "ranks")) == {
            role: rank for _, role, _, rank in expected
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
        assert status == 200 and b"Pair next varsity round (teams and rooms)" in body
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
        def page_without_csrf(path):
            body = request(port, path, extra_headers=jar)[2]
            return re.sub(rb'name="csrfmiddlewaretoken" value="[A-Za-z0-9]{64}"', b'name="csrfmiddlewaretoken"', body)

        before = {path: page_without_csrf(path) for path in paths}
        assert b"(chair)" in before[inrounds] and b"New Chair" in before[inrounds]
        assert b"manual edits" in before[outrounds]
        assert b"New Chair" in request(port, f"{inrounds}?manual=1", extra_headers=jar)[2]
        for body in before.values():
            assert b"protected" not in body and b"private reason fixture" not in body
            assert b"Synthetic-Board" not in body

        Scratch.objects.create(team=teams[0], judge=judges[0], scratch_type=Scratch.TAB_SCRATCH)
        Judge.objects.filter(id=judges[0].id).update(rank=Decimal("9.57"), wing_only=True,
                                                   request_wing=True, no_wings=True)
        assert {path: page_without_csrf(path) for path in paths} == before
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
        assert correction(final.id, {"action": "room", "room": rooms[2].id})[0] == 302
        assert correction(first.id, {"action": "result", "victor": 0})[0] == 409
        assert Outround.objects.get(id=first.id).victor == Outround.GOV
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
        "custom_staff-INITIAL_FORMS": "0",
        "custom_staff-0-title": "Room Coordinator",
        "custom_staff-0-names": "Robin Coordinator",
    }
    with mock_school_directory() as endpoint, rust_server(overrides={"MITTAB_SCHOOL_DIRECTORY_URL": endpoint}) as port:
        assert request(port, "/", cookie=tab)[1]["Location"] == basics
        assert request(port, todo, cookie=tab)[1]["Location"] == basics
        assert request(port, basics, cookie=board)[0] == 403
        status, headers, page = request(port, basics, cookie=tab)
        assert status == 200 and b"Complete this form to unlock staff tools." in page
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
        assert b"Day Of Tournament" in page and b"Speaker Results Auto-Published" in page
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

        replacement = {**ready, "host_school": f"pk:{school.id}", "custom_staff-INITIAL_FORMS": "1",
                       "custom_staff-0-DELETE": "on"}
        for missing in ("custom_staff-TOTAL_FORMS", "custom_staff-INITIAL_FORMS"):
            assert post(basics, {key: value for key, value in replacement.items() if key != missing})[0] == 422
            assert TournamentStaffRole.objects.get().title == "Room Coordinator"
        stale = post(basics, {**replacement, "custom_staff-INITIAL_FORMS": "0"})
        assert stale[0] == 422 and b"Other staff roles changed" in stale[2]
        assert TournamentStaffRole.objects.get().title == "Room Coordinator"
        edited = {key: value for key, value in replacement.items() if key != "custom_staff-0-DELETE"}
        for missing in ("custom_staff-0-title", "custom_staff-0-names"):
            assert post(basics, {key: value for key, value in edited.items() if key != missing})[0] == 422
            assert TournamentStaffRole.objects.get().title == "Room Coordinator"
        assert post(basics, replacement)[1]["Location"] == basics
        assert TabSettings.get("host_school_name", use_cache=False) == "Local Host"
        assert TabSettings.get("tournament_date", use_cache=False) == tournament_date
        assert not TournamentStaffRole.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_tournament_basics_matches_django_form_markup(html_assets, subtests):
    from mittab.apps.tab.models import TournamentStaffRole

    staff = legacy_tab_session("setup-html-staff")
    path = "/setup/start-tournament/"
    school = School.objects.create(name="Local & School")
    TabSettings.set("cur_round", 1)
    TabSettings.set("start_tournament_wizard_completed", 0)
    TabSettings.set("start_tournament_staff_invites_sent", 2)
    TabSettings.set("tournament_name", "New Tournament")
    TabSettings.set("theme_color", "#225577")
    InfoText.objects.create(title="GA", text="TBD", is_active=True)
    InfoLink.objects.create(title="Facebook", url="https://example.test/announcement", is_active=True)
    csrf = "a" * 32
    client = Client()
    client.cookies["sessionid"] = staff
    client.cookies["csrftoken"] = csrf
    cookie = {"Cookie": f"sessionid={staff}; csrftoken={csrf}"}

    def compare(port, fields=None):
        form = None if fields is None else {"csrfmiddlewaretoken": csrf, **fields}
        status, _, body = request(port, path, extra_headers=cookie,
                                  method="GET" if fields is None else "POST", form=form)
        assert status == (200 if fields is None else 422)
        token = re.search(rb'name="csrfmiddlewaretoken" value="([A-Za-z0-9]{64})"', body)[1]
        with patch("django.middleware.csrf._get_new_csrf_string", return_value=token[:32].decode()):
            reference = client.get(path) if fields is None else client.post(path, form)
        assert reference.status_code == 200
        assert_html_bytes(body.replace(b'name="wizard_action" value="send_invite"',
                                      b'name="action" value="send_invite"'), reference.content)
        return body

    with patch("mittab.apps.registration.services.fetch_remote_schools", return_value=[]), \
            rust_server(overrides={**html_assets, "MITTAB_SCHOOL_DIRECTORY_URL": ""}) as port:
        with subtests.test(state="initial"):
            body = compare(port)
            assert b"2 tab staff invitations sent." in body and b"example.test/announcement" not in body
        with subtests.test(state="bound invalid"):
            compare(port, {
                "action": "save", "tournament_name": "Test & <Tournament>", "host_school": f"pk:{school.id}",
                "tournament_type": "0", "sanctioned": "on", "ga_room": "  ", "facebook_url": "bad:url",
                "tournament_director": "First Person", "tab_director": "Second Person",
                "equity_officers": "Third Person\nFourth Person", "tournament_observer": "",
                "open_speaks": "3", "open_ranks": "2",
                "custom_staff-TOTAL_FORMS": "2", "custom_staff-INITIAL_FORMS": "0",
                "custom_staff-MIN_NUM_FORMS": "0", "custom_staff-MAX_NUM_FORMS": "1000",
                "custom_staff-0-title": "Room <Director>", "custom_staff-0-names": "",
                "custom_staff-1-title": "Removed role", "custom_staff-1-DELETE": "on",
            })
            assert TabSettings.get("start_tournament_wizard_completed", use_cache=False) == 0
            assert not TournamentStaffRole.objects.exists()
        with subtests.test(state="invite invalid"):
            compare(port, {"action": "send_invite", "invite-permission_preset": "checkin_helper"})
            for invalid in ({}, {"invite-email": "bad-address", "invite-username": "valid-user",
                                "invite-permission_preset": "invalid-preset"}):
                form = {"action": "send_invite", "csrfmiddlewaretoken": csrf, **invalid}
                status, _, body = request(port, path, method="POST",
                    extra_headers={**cookie, "X-Requested-With": "XMLHttpRequest"}, form=form)
                assert status == 400
                reference = client.post(path, form, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                assert json.loads(body) == reference.json()
            multipart = "".join(
                f'--fixture\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
                for key, value in {"wizard_action": "send_invite", "csrfmiddlewaretoken": csrf,
                                   "invite-email": "bad-address", "invite-username": "valid-user",
                                   "invite-permission_preset": "invalid-preset"}.items()
            ) + "--fixture--\r\n"
            status, _, body = request(port, path, method="POST",
                extra_headers={**cookie, "X-Requested-With": "XMLHttpRequest",
                               "Content-Type": "multipart/form-data; boundary=fixture"},
                raw_body=multipart)
            assert status == 400 and json.loads(body) == reference.json()
        with subtests.test(state="populated"):
            TabSettings.set("start_tournament_wizard_completed", 1)
            TabSettings.set("tournament_name", "Invitational 'A' & 'B'")
            TabSettings.set("host_school", "apda:77")
            TabSettings.set("host_school_name", "Historic Host")
            TabSettings.set("open_speaks", 2)
            TabSettings.set("open_ranks", 1)
            TabSettings.set("equity_officers", "Equity Person\nSecond Person")
            InfoText.objects.filter(title="GA").update(text="Main & West")
            TournamentStaffRole.objects.create(title="Runner & <coord>", names="One\nTwo", sort_order=0)
            body = compare(port)
            assert b"Historic Host" in body and b"example.test/announcement" in body
            assert b"staff-invite-form" not in body


@pytest.mark.django_db(transaction=True)
def test_checklist_matches_django_html_and_autosave_behavior(html_assets, subtests):
    from mittab.apps.tab.models import UserTournamentSetupPreference

    staff = legacy_tab_session("checklist-html")
    colleague = legacy_tab_session("checklist-colleague")
    path = "/setup/tournament-todo/"
    toggle = f"{path}toggle/"
    TabSettings.set("start_tournament_wizard_completed", 1)
    TabSettings.set("cur_round", 1)
    TabSettings.set("tot_rounds", 5)
    TabSettings.set("tournament_name", "New Tournament")
    csrf = "a" * 32
    client = Client()
    client.cookies["sessionid"] = staff
    client.cookies["csrftoken"] = csrf
    cookie = {"Cookie": f"sessionid={staff}; csrftoken={csrf}"}

    def compare(port, target=path):
        status, _, body = request(port, target, extra_headers=cookie)
        assert status == 200
        for cache in ("default", "filesystem"):
            caches[cache].clear()
        token = re.search(rb'name="csrfmiddlewaretoken" value="([A-Za-z0-9]{64})"', body)[1]
        with patch("django.middleware.csrf._get_new_csrf_string", return_value=token[:32].decode()):
            reference = client.get(target)
        assert reference.status_code == 200
        assert_html_bytes(body, reference.content)
        return body

    def autosave(port, field, checked):
        form = {"field": field, "checked": checked, "csrfmiddlewaretoken": csrf}
        status, _, payload = request(port, toggle, method="POST", extra_headers=cookie, form=form)
        for cache in ("default", "filesystem"):
            caches[cache].clear()
        reference = client.post(toggle, form)
        assert status == reference.status_code == 200
        assert json.loads(payload) == reference.json()
        return reference.json()

    with rust_server(overrides=html_assets) as port:
        with subtests.test(page="new tournament"):
            body = compare(port, path + "?return_to=%2Fsettings_form&next=%2Fpublic%2F")
            assert b'data-autosave-url="/setup/tournament-todo/toggle/"' in body
            assert b'name="return_to" value="/settings_form"' in body
            assert b'data-completed="0"' in body
        with subtests.test(page="manual and automatic progress"):
            TabSettings.set("tournament_name", "Founders & Alumni")
            room = Room.objects.create(name="Access room", rank=1)
            tag = RoomTag.objects.create(tag="Accessible", priority=1)
            room.tags.add(tag)
            activity = autosave(port, "step_add_tab_user_accounts", "1")
            assert activity["section_completed"] == 3 and activity["section_progress_percent"] == 42
            automatic = autosave(port, "step_set_tournament_name", "0")
            assert automatic["checked"] and automatic["auto_completed"]
            body = compare(port)
            assert b'aria-label="About Add Accessible Room Tags"' in body
            assert b'data-completed="3"' in body
        with subtests.test(page="persistent personal preference"):
            assert autosave(port, "hide_tournament_todo", "true")["checked"]
            body = compare(port)
            assert re.search(rb'id="hide_tournament_todo"[^>]*checked', body)
            peer = request(port, path, cookie=colleague)[2]
            assert not re.search(rb'id="hide_tournament_todo"[^>]*checked', peer)
            assert b'data-completed="3"' in peer
            assert UserTournamentSetupPreference.objects.get(user__username="checklist-html").hide_tournament_todo


@pytest.mark.django_db(transaction=True)
def test_native_settings_write_only_declared_fields_and_honor_discretionary_scratch_lock(html_assets):
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

    with rust_server(overrides=html_assets) as port:
        status, headers, page = request(port, path, cookie=tab)
        assert status == 200
        assert b"Tab Settings" in page and b"Speaker Points" in page
        assert re.search(rb'id="id_setting_judges_public"[^>]*disabled', page)
        csrf = load_cookies(headers)["csrftoken"].value
        jar = {"Cookie": f"sessionid={tab}; csrftoken={csrf}"}
        client = Client()
        client.cookies["sessionid"] = tab
        client.cookies["csrftoken"] = csrf

        def compare(body, form=None):
            caches["default"].clear()
            caches["filesystem"].clear()
            mask = re.search(rb'name="csrfmiddlewaretoken" value="([A-Za-z0-9]{64})"', body)[1][:32]
            with patch("django.middleware.csrf._get_new_csrf_string", return_value=mask.decode()):
                reference = client.get(path) if form is None else client.post(path, form)
            assert reference.status_code == 200
            assert_html_bytes(body, reference.content)

        compare(page)

        def post(path, form):
            return request(port, path, method="POST", extra_headers=jar,
                           form={"csrfmiddlewaretoken": csrf, **form})

        invalid = {**data, "setting_min_byes": "-1", "setting_written_rfd_deadline": "not-a-date",
                   "setting_outround_pairing_mode": "unexpected & invalid"}
        status, _, body = post(path, invalid)
        assert status == 422
        compare(body, invalid)
        assert TabSettings.get("tot_rounds", use_cache=False) == 5
        all_categories = [category["id"] for category in categories]
        visited = ",".join([*all_categories, "unknown"])
        assert post(path, {**data, "setting_cur_round": "10", "visited_categories": visited})[1]["Location"] == path
        assert TabSettings.get("settings_tabs_seen", use_cache=False) == ",".join(sorted(all_categories))
        assert TabSettings.get("tot_rounds", use_cache=False) == 7
        assert TabSettings.get("cur_round", use_cache=False) == 1
        assert TabSettings.get("judges_public", use_cache=False) == 1
        assert TabSettings.get("written_rfd_deadline", use_cache=False) == "2099-11-10 20:15"
        registration.disc_scratches_open = False
        registration.save(update_fields=["disc_scratches_open"])
        assert post(path, data)[0] == 302
        assert TabSettings.get("judges_public", use_cache=False) == 0
        compare(request(port, path, extra_headers=jar)[2])
        TabSettings.objects.filter(key="teams_public").update(value=None, value_string="1")
        without_roster_flag = {key: value for key, value in data.items() if key != "setting_teams_public"}
        assert post(path, without_roster_flag)[0] == 302
        assert TabSettings.get("teams_public", use_cache=False) == "1"
        assert post(path, {**without_roster_flag, "setting_teams_public": "0"})[0] == 302
        assert TabSettings.get("teams_public", use_cache=False) == 0
        viewed = f"{path}/viewed/"
        assert post(viewed, {"category": "invalid"})[0] == 400
        for category in categories:
            assert post(viewed, {"category": category["id"]})[0] == 204
        page = request(port, "/setup/tournament-todo/", extra_headers=jar)[2]
        assert re.search(rb'id="step_configure_settings"[^>]* checked[^>]* disabled', page)


@pytest.mark.django_db(transaction=True)
def test_staff_round_list_matches_django_bytes(html_assets):
    path = "/pairings/view_rounds/"
    TabSettings.set("start_tournament_wizard_completed", 1)
    TabSettings.set("cur_round", 2)
    session = legacy_tab_session("round-list-parity")
    client = Client()
    client.cookies["sessionid"] = session
    with rust_server(overrides=html_assets) as port:
        for rounds in (0, 5):
            TabSettings.set("tot_rounds", rounds)
            status, _, body = request(port, path, cookie=session)
            reference = client.get(path)
            assert status == reference.status_code == 200
            assert_html_bytes(body, reference.content)


@pytest.mark.django_db(transaction=True)
def test_batch_checkin_and_history_match_django_bytes(html_assets):
    from mittab.apps.tab.models import BulkCheckInChange

    TabSettings.set("start_tournament_wizard_completed", 1)
    TabSettings.set("cur_round", 1)
    TabSettings.set("tot_rounds", 3)
    TabSettings.set("theme_color", "#A24260")
    TabSettings.set("motions_enabled", 0)
    TabSettings.set("debaters_public", 1)
    primary = School.objects.create(name="Example & School")
    secondary = School.objects.create(name="Another School")
    team = Team.objects.create(name="Zebra", school=primary, seed=Team.UNSEEDED, checked_in=True)
    Team.objects.create(name="Albatross", school=secondary, seed=Team.UNSEEDED, checked_in=False)
    team.debaters.add(Debater.objects.create(name="Z. Sample", novice_status=0), Debater.objects.create(name="A. O'Example", novice_status=0))
    judge = Judge.objects.create(name="Judge <Tag>", rank=4, wing_only=True, request_wing=True, no_wings=True)
    judge.schools.add(primary, secondary)
    Judge.objects.create(name="Unaffiliated judge", rank=3)
    CheckIn.objects.create(judge=judge, round_number=1)
    RoomCheckIn.objects.create(room=Room.objects.create(name="Example Room", rank=1), round_number=3)
    JudgeExpectedCheckIn.objects.create(judge=judge, round_number=0)
    JudgeExpectedCheckIn.objects.create(judge=judge, round_number=2)

    for index in range(26):
        BulkCheckInChange.objects.create(
            entity_type="judge_expected", cells=[[judge.id, 0], [judge.id, 2]],
            checked=True, undone=index < 25,
        )
    current = BulkCheckInChange.objects.latest("id")
    # Include an exact second, a fractional second, and a September date.
    BulkCheckInChange.objects.filter(pk=current.pk).update(
        created_at=datetime(2025, 9, 7, 8, 15, 5, 123456, tzinfo=timezone.get_default_timezone()),
    )
    BulkCheckInChange.objects.filter(undone=True).update(
        created_at=datetime(2025, 1, 5, 16, 14, 3, tzinfo=timezone.get_default_timezone()),
    )
    page = "/batch_checkin/"
    history = f"{page}?history=judge_expected"
    csrf = "a" * 32
    clients = []
    for superuser in (True, False):
        user = get_user_model().objects.create_user(
            username=f"checkin-html-{superuser}", is_superuser=superuser, is_staff=True,
        )
        if not superuser:
            user.groups.add(Group.objects.create(name="MIT-TAB: Check-in helper"))
        client = Client()
        client.force_login(user)
        client.cookies["csrftoken"] = csrf
        clients.append(client)

    paths = [
        page, f"{page}?history=room", history, f"{history}&page=2", f"{history}&page=invalid",
        f"{history}&page=0", f"{history}&before={current.token}", f"{history}&before=missing",
    ]

    def compare(port, path, client):
        cookie = client.cookies["sessionid"].value
        status, _, body = request(
            port, path, extra_headers={"Cookie": f"sessionid={cookie}; csrftoken={csrf}"},
        )
        token = re.search(rb'name="csrfmiddlewaretoken" value="([A-Za-z0-9]{64})"', body)
        mask = token[1][:32].decode() if token else csrf
        with patch("django.middleware.csrf._get_new_csrf_string", return_value=mask):
            reference = client.get(path)
        assert status == reference.status_code, (path, status)
        assert_html_bytes(body, reference.content)

    with rust_server(overrides=html_assets) as port:
        for client in clients:
            for path in paths:
                compare(port, path, client)
        TabSettings.set("debaters_public", 0)
        compare(port, page, clients[0])
        TabSettings.set("tot_rounds", 0)
        compare(port, page, clients[0])


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
        assert b"Reverse 2 edits" in preview and b"Set 1 status to Checked Out." in preview
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
        notice = load_cookies(restore[1])["mittab_checkins_restored"]
        assert notice["httponly"]
        status, headers, body = request(
            port, page_url, extra_headers={
                "Cookie": f"sessionid={helper_cookie}; csrftoken={csrf}; {notice.key}={notice.value}",
            },
        )
        assert status == 200 and b"Reversed 2 check-in edits." in body
        assert b'class="alert alert-success"' in body
        assert load_cookies(headers)[notice.key]["max-age"] == "0"
        assert b"Reversed 2 check-in edits." not in request(port, page_url, cookie=helper_cookie)[2]
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
    extra_speaker = Debater.objects.create(name="A Ranking Speaker", novice_status=0)
    teams[0].debaters.add(extra_speaker)
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

    with rust_server(overrides={"MITTAB_SHARED_PUBLIC_CACHE": "1"}) as port:
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
        status, headers, teams_page = request(port, standings)
        assert status == 200
        assert headers["Vary"] == "Cookie"
        assert headers["Cache-Control"] == "public, max-age=10, s-maxage=10, stale-while-revalidate=10"
        assert request(port, standings)[2] == teams_page
        assert b"Top Example" in teams_page and b"Private Example" not in teams_page
        assert b"A Ranking Speaker, Ranking Speaker 0" in teams_page
        assert b"31.75" not in teams_page and b"Speaks</th>" not in teams_page
        vars_page = request(port, speakers_page)[2]
        assert b"Varsity Speakers" in vars_page and b"Novice Speakers" not in vars_page
        assert b"Ranking Speaker 0" in vars_page and b"Ranking Speaker 1" not in vars_page
        round_page = request(port, ballots)[2]
        assert b"Ranking Speaker 0" in round_page and b"Ranking Speaker 1" in round_page
        assert b"31.75" not in round_page and b"R 1.00" in round_page
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
        assert b"31.75" in request(port, ballots)[2]
        assert b'colspan="2">Unadjusted</th>' in request(port, speakers_page)[2]

        RoundStats.objects.filter(round=first, debater=people[0]).update(speaks=Decimal("32.25"))
        assert b"32.25" in request(port, standings)[2]
        TabSettings.set("debaters_public", 0)
        hidden_debaters = request(port, standings)[2]
        assert b"Top Example" in hidden_debaters and b"Ranking Speaker" not in hidden_debaters
        Team.objects.filter(pk=teams[0].pk).update(ranking_public=False)
        assert b"Top Example" not in request(port, standings)[2]
        assert post({**policy, "team_public": ""})[0] == 302
        for session in (None, board):
            status, headers, _ = request(port, standings, cookie=session)
            assert status == 302 and headers["Location"] == "/public/access-error/"


@pytest.mark.django_db(transaction=True)
def test_native_public_outrounds_match_the_visible_django_bracket_payload(html_assets, subtests):
    from mittab.libs.bracket_display_logic import generate_bracket_data

    TabSettings.set("cur_round", 3)
    TabSettings.set("tot_rounds", 2)
    TabSettings.set("var_teams_visible", 8)
    TabSettings.set("debaters_public", 1)
    school = School.objects.create(name="Outrounds School")
    teams = {}
    for seed in range(1, 7):
        name = "Outround </script> '≤' 🎤 Team" if seed == 5 else f"Outround Team {seed}"
        team = Team.objects.create(name=name, school=school, seed=Team.UNSEEDED)
        team.debaters.add(Debater.objects.create(name=f"Outround Speaker {seed} & Co", novice_status=0))
        BreakingTeam.objects.create(team=team, seed=seed, effective_seed=seed, type_of_team=0)
        teams[seed] = team
    chair = Judge.objects.create(name="Public Outround Chair", rank=9)
    wing = Judge.objects.create(name="Public Outround Wing", rank=8)
    hidden = Judge.objects.create(name="Undisclosed Outround Judge", rank=10, wing_only=True)
    room = Room.objects.create(name="Public Outround Room", rank=10)
    first = Outround.objects.create(num_teams=8, gov_team=teams[5], opp_team=teams[4],
                                    victor=1, room=room, sidelock=True, choice=Outround.GOV)
    first.judges.add(chair, wing)
    Outround.objects.filter(id=first.id).update(chair=chair)
    other = Outround.objects.create(num_teams=8, gov_team=teams[3], opp_team=teams[6], victor=1)
    next_round = Outround.objects.create(num_teams=4, gov_team=teams[1], opp_team=teams[5])
    next_round.judges.add(hidden)
    novice_teams = [
        Team.objects.create(name=f"Novice Outround Team {index}", school=school, seed=0)
        for index in range(1, 3)
    ]
    for seed, team in enumerate(novice_teams, start=1):
        BreakingTeam.objects.create(team=team, seed=seed, effective_seed=seed, type_of_team=Outround.NOVICE)
    Outround.objects.create(num_teams=2, type_of_round=Outround.NOVICE,
                            gov_team=novice_teams[0], opp_team=novice_teams[1], victor=Outround.OPP_VIA_FORFEIT)
    TabSettings.set("gov_opp_display", 1)
    TabSettings.set("sidelock", 1)
    TabSettings.set("choice", 1)
    board = legacy_tab_session("public-outs-board")
    get_user_model().objects.get(username="public-outs-board").groups.add(Group.objects.create(name="APDA Board"))
    varsity = "/public/outrounds/0/"
    novice = "/public/outrounds/1/"

    def payload(page):
        match = re.search(rb"window\.bracketViewerData = JSON\.parse\('([^']*)'\);", page)
        return json.loads(json.loads(b'"' + match[1] + b'"')) if match else None

    def compare(port, path, session=None):
        for alias in ("default", "filesystem"):
            caches[alias].clear()
        client = Client()
        if session:
            client.cookies["sessionid"] = session
        reference = client.get(path)
        status, _, body = request(port, path, cookie=session)
        assert status == reference.status_code == 200
        assert_html_bytes(body, reference.content)
        return body

    with rust_server(overrides=html_assets) as port:
        assert request(port, novice)[1]["Location"] == "/public/access-error/"
        for session in (None, legacy_tab_session("public-outs-staff"), board):
            with subtests.test(audience=session is None, board=session == board):
                compare(port, varsity, session)
        page = request(port, varsity, cookie=board)[2]
        assert b"[V] Ro8" in page and b"[V] Ro4" not in page
        assert re.search(rb'Advancing Teams:</b></td>\s*<td colspan="3">\s*Outround Team 1', page)
        assert b"<b>Public Outround Chair</b>" in page
        assert b"Undisclosed Outround Judge" not in page and b"Outround </script>" not in page
        pairings = list(Outround.objects.filter(pk__in=(first.id, other.id)).order_by("id"))
        assert payload(page) == json.loads(json.dumps(generate_bracket_data([{"rounds": pairings}])))
        Scratch.objects.create(team=teams[4], judge=chair, scratch_type=Scratch.TAB_SCRATCH)
        Judge.objects.filter(id=chair.id).update(rank=1, wing_only=True, request_wing=True)
        # Scoring a hidden debate must not affect the advancing-team line on the public page.
        Outround.objects.filter(pk=next_round.id).update(victor=Outround.OPP)
        assert request(port, varsity, cookie=board)[2] == page
        Outround.objects.filter(pk=next_round.id).update(victor=Outround.UNKNOWN)
        assert request(port, varsity, cookie=board, method="POST")[1]["Location"] == "/403/"

        TabSettings.set("var_teams_visible", 4)
        updated = compare(port, varsity)
        assert b"[V] Ro4" in updated and b"Undisclosed Outround Judge" in updated
        expected = generate_bracket_data([{"rounds": pairings}, {"rounds": [next_round]}])
        assert payload(updated) == json.loads(json.dumps(expected))
        TabSettings.set("debaters_public", 0)
        TabSettings.set("nov_teams_visible", 2)
        confidential = compare(port, varsity)
        assert b"Outround Speaker" not in confidential
        assert b"Outround Team 1" in confidential
        assert b"[N] Ro2" in compare(port, novice)
        TabSettings.set("show_outs_bracket", 0)
        assert payload(compare(port, novice)) is None
        TabSettings.set("show_outs_bracket", 1)
        Outround.objects.filter(type_of_round=Outround.NOVICE).update(num_teams=1)
        TabSettings.set("nov_teams_visible", 1)
        status, _, unavailable_bracket = request(port, novice)
        assert status == 200 and b"[N] Ro1" in unavailable_bracket
        assert payload(unavailable_bracket) is None and b"No bracket data available." in unavailable_bracket
        TabSettings.set("outround_pairing_mode", 1)
        assert payload(compare(port, varsity)) is None


@pytest.mark.django_db(transaction=True)
def test_public_preliminary_pairings_and_missing_ballots_match_django_bytes(html_assets, subtests):
    path = "/public/pairings/"
    missing_path = "/public/missing-ballots/"
    school = School.objects.create(name="Visible Pairings School")
    terms = ("Zeta & Co", "North", "Alpha", "East", "Bye Team", "Unpaired Team")
    teams = [Team.objects.create(name=name, school=school, seed=0, checked_in=True) for name in terms]
    for number, team in enumerate(teams, start=1):
        team.debaters.add(Debater.objects.create(name=f"Public Speaker {number}", novice_status=0))
    wing = Judge.objects.create(name="Public Earlier Wing", rank=5)
    chair = Judge.objects.create(name="Public Later Chair", rank=9)
    room = Room.objects.create(name="Public & Released", rank=3)
    Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1], victor=Round.GOV)
    first = Round.objects.create(round_number=2, gov_team=teams[0], opp_team=teams[3], chair=chair, room=room)
    first.judges.add(chair, wing)
    second = Round.objects.create(round_number=2, gov_team=teams[2], opp_team=teams[1], victor=Round.OPP)
    Bye.objects.create(round_number=2, bye_team=teams[4])
    TabSettings.set("cur_round", 3)
    TabSettings.set("tot_rounds", 2)
    TabSettings.set("debaters_public", 1)
    TabSettings.set("pairing_released", 0)
    board = legacy_tab_session("public-pairings-board")
    get_user_model().objects.get(username="public-pairings-board").groups.add(Group.objects.create(name="APDA Board"))
    previous = f"{path}1/"

    with rust_server(overrides=html_assets) as port:
        cases = (
            (path, {}),
            (previous, {}),
            (path, {"pairing_released": 1}),
            (missing_path, {}),
            (path, {"debaters_public": 0}),
            (missing_path, {"finish": True}),
        )
        assert request(port, previous, cookie=board)[0] == 403
        assert request(port, missing_path)[1]["Location"] == "/public/access-error/"
        for target, changes in cases:
            with subtests.test(path=target, changes=changes):
                for key, value in changes.items():
                    if key == "finish":
                        Round.objects.filter(pk__in=(first.id, second.id)).update(victor=Round.GOV)
                    else:
                        TabSettings.set(key, value)
                caches["default"].clear()
                status, _, body = request(port, target)
                golden = Client().get(target)
                assert status == golden.status_code == 200
                assert_html_bytes(body, golden.content)
                if target == path and changes == {"debaters_public": 0}:
                    assert b"Public Speaker" not in body and b"Public Earlier Wing" in body

        with subtests.test(audience="Board"):
            client = Client()
            client.cookies["sessionid"] = board
            status, headers, body = request(port, path, cookie=board)
            reference = client.get(path)
            assert status == reference.status_code == 200
            assert headers["Cache-Control"] == "private, no-store"
            assert_html_bytes(body, reference.content)
            assert request(port, path, method="HEAD", cookie=board)[0] == 200
            for target in (previous, "/public/access-error/"):
                assert request(port, target, cookie=board)[1]["Location"] == client.get(target)["Location"] == "/403/"
            assert b"<b>Public Later Chair</b>" in body
            scratch = Scratch.objects.create(team=teams[0], judge=chair, scratch_type=Scratch.TAB_SCRATCH)
            Judge.objects.filter(pk=chair.id).update(rank=8, wing_only=True, request_wing=True,
                                                     ballot_code="Private-Example-Fixture")
            assert request(port, path, cookie=board)[2] == body
            assert b"Private-Example-Fixture" not in body
            assert request(port, "/public/e-ballots/Private-Example-Fixture/", cookie=board)[1]["Location"] == "/403/"
            assert request(port, path, method="POST", cookie=board, form={"judge": wing.id})[1]["Location"] == "/403/"
            first.refresh_from_db()
            assert first.chair_id == chair.id and set(first.judges.values_list("id", flat=True)) == {chair.id, wing.id}
            assert Scratch.objects.filter(pk=scratch.id, judge_id=chair.id, team_id=teams[0].id).exists()


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
        assert status == 200 and b"Send invitation" in body
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
        current = request(port, "/round/1/", cookie=tab)[2]
        assert b'class="gov-team manual-team"' in current or b'class="opp-team manual-team"' in current
        assert b'judge-assignment manual-lay' in current
        assert b"manual edits" in current
        ManualJudgeAssignment.objects.filter(round=debate).delete()
        AuditEvent.record(debate, "judges_paired", None)
        after_reset = request(port, "/round/1/", cookie=tab)[2]
        assert b'class="gov-team manual-team"' in after_reset or b'class="opp-team manual-team"' in after_reset
        assert b'judge-assignment manual-lay' not in after_reset
        assert b"manual edits" not in after_reset

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
def test_native_staff_standard_break_panels_publication_and_next_stage(tmp_path, html_assets):
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

    with rust_server(overrides={**html_assets, "BACKUP_PREFIX": str(tmp_path), "BACKUP_STORAGE": ""}) as port:
        start = "/break/"
        status, headers, body = request(port, start, cookie=tab)
        assert status == 200 and b"Pairing Outrounds" in body
        csrf = load_cookies(headers)["csrftoken"].value
        jar = {"Cookie": f"sessionid={tab}; csrftoken={csrf}"}
        reference = Client()
        reference.cookies["sessionid"] = tab
        reference.cookies["csrftoken"] = csrf
        room_check = RoomCheckIn.objects.order_by("pk").last()
        for available in (3, 2):
            if available == 2:
                room_check.delete()
            status, _, body = request(port, start, extra_headers=jar)
            assert status == 200 and f"Need 3, have {available}".encode() in body
            mask = re.search(rb'name="csrfmiddlewaretoken" value="([A-Za-z0-9]{64})"', body)[1][:32]
            with patch("django.middleware.csrf._get_new_csrf_string", return_value=mask.decode()):
                assert_html_bytes(body, reference.get(start).content)
        RoomCheckIn.objects.create(room_id=room_check.room_id, round_number=0)

        def post(path, fields=None, session=tab):
            return request(port, path, method="POST",
                           extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": csrf, **(fields or {})})

        not_ready = post(start)
        assert not_ready[0] == 422 and b"Enter all preliminary outcomes" in not_ready[2]
        assert not BreakingTeam.objects.exists() and not list(tmp_path.glob("*.dump.sql"))
        Round.objects.filter(id=preliminary.id).update(victor=Round.GOV)
        result = post(start)
        assert result[0] == 302 and result[1]["Location"] == "/outround_pairing", result
        assert BreakingTeam.objects.count() == 6
        assert Outround.objects.filter(type_of_round=0, num_teams=4).count() == 2
        assert Outround.objects.filter(type_of_round=1, num_teams=2).count() == 1
        assert not Outround.objects.exclude(chair=None, room=None).exists()
        assert list(tmp_path.glob("Before the Break*.dump.sql"))
        unconfirmed = post(start)
        assert unconfirmed[0] == 422 and b'name="confirm"' in unconfirmed[2]
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
        def replacement_token(body):
            return re.search(rb'name="break_revision" value="([a-f0-9]{64})"', body)[1].decode()

        first_revision = replacement_token(post(start)[2])
        Outround.objects.filter(pk=final.id).update(choice=Outround.GOV)
        conflict = post(start, {"confirm": "on", "break_revision": first_revision})
        assert conflict[0] == 422 and Outround.objects.filter(pk=final.id, choice=Outround.GOV).exists()
        updated_revision = replacement_token(conflict[2])
        assert updated_revision != first_revision
        assert json.loads(post("/outround_pairing/release/2/1/")[2])["pairing_released"]
        released = post(start, {"confirm": "on", "break_revision": updated_revision})
        assert released[0] == 422 and TabSettings.get("nov_teams_visible", use_cache=False) == 2
        confirmed = replacement_token(released[2])
        assert confirmed != updated_revision
        assert post(start, {"confirm": "on", "break_revision": confirmed})[0] == 302
        assert not Outround.objects.filter(pk=final.id).exists()
        assert post(start, {"confirm": "on", "break_revision": confirmed})[0] == 422


@pytest.mark.django_db(transaction=True)
def test_elimination_staff_and_board_pages_match_django_bytes(html_assets, subtests):
    from mittab.apps.tab.models import OutroundChallenge

    staff = legacy_tab_session("elimination-html")
    board = legacy_tab_session("elimination-html-board")
    user = get_user_model().objects.get(username="elimination-html")
    get_user_model().objects.get(username="elimination-html-board").groups.add(Group.objects.create(name="APDA Board"))
    for key, value in {
        "cur_round": 2, "tot_rounds": 1, "var_teams_to_break": 4, "nov_teams_to_break": 2,
        "var_panel_size": 3, "nov_panel_size": 2, "choice": 1, "var_teams_visible": 4,
        "outround_pairing_mode": 0,
    }.items():
        TabSettings.set(key, value)
    school = School.objects.create(name="Example & Company")
    names = ["North & East", "South", "West", "Northwest", "Varsity Alternate", "Novice North", "Novice South"]
    teams = [Team.objects.create(name=name, school=school, seed=0) for name in names]
    for index, team in enumerate(teams):
        team.debaters.add(Debater.objects.create(name=f"Avery {index}", novice_status=0),
                          Debater.objects.create(name=f"Taylor {index}", novice_status=0))
        BreakingTeam.objects.create(team=team, type_of_team=int(index >= 5), seed=index + 1, effective_seed=index + 1)
    Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1], victor=Round.GOV)
    first_room = Room.objects.create(name="Room & 101", rank=3)
    second_room = Room.objects.create(name="Room 205", rank=4)
    for room in (first_room, second_room):
        RoomCheckIn.objects.create(room=room, round_number=0)
    chair = Judge.objects.create(name="Casey & Co", rank=Decimal("7.25"), request_wing=True)
    wing = Judge.objects.create(name="Avery Wing", rank=Decimal("4.75"), wing_only=True)
    unavailable = Judge.objects.create(name="Absent Judge", rank=Decimal("8.00"))
    eligible = Judge.objects.create(name="Eligible Judge", rank=5)
    for judge in (chair, wing, eligible):
        CheckIn.objects.create(judge=judge, round_number=0)
    var = Outround.objects.create(num_teams=4, type_of_round=0, gov_team=teams[0], opp_team=teams[1],
                                  chair=chair, room=first_room, choice=Outround.GOV)
    var.judges.add(chair, wing)
    ended = Outround.objects.create(num_teams=4, type_of_round=0, gov_team=teams[2], opp_team=teams[3], victor=Outround.OPP)
    nov = Outround.objects.create(num_teams=2, type_of_round=1, gov_team=teams[5], opp_team=teams[6], room=second_room)
    nov.judges.add(eligible)
    needed = RoomTag.objects.create(tag="Accessible", priority=1)
    teams[0].required_room_tags.add(needed)
    second_room.tags.add(needed)
    AuditEvent.record(var, AuditEvent.MANUAL_CHAIR_CHANGE, user,
                      changes={"new_chair": chair.name}, note="Private staff-only note")
    AuditEvent.record(var, AuditEvent.EDIT, user, changes={"public": {
        "source": "staff team correction",
        "before": {"gov_team": {"id": teams[4].id, "name": teams[4].name}},
        "after": {"gov_team": {"id": teams[0].id, "name": teams[0].name}},
    }})
    selected = "?select_varsity=4&select_novice=2"
    paths = [
        "/outround_pairing/0/4", "/outround_pairing/0/4?select_varsity=4&select_novice=",
        f"/outround_pairing/1/2{selected}", "/outround_pairing/0/4?select_varsity=99",
        f"/outround/{var.id}/{teams[0].id}/alternative_teams/gov/",
        f"/outround/{var.id}/alternative_judges/{chair.id}/{selected}",
        f"/outround/{var.id}/alternative_judges/{selected}",
        f"/outround/{var.id}/alternative_rooms/{first_room.id}/{selected}",
        "/outround_pairing/pair/0/4/", "/outround_result/0",
        f"/outround/{var.id}/result/", f"/outround/{ended.id}/result/",
    ]

    def compare(port, path, session):
        client = Client()
        client.cookies["sessionid"] = session
        client.cookies["csrftoken"] = "a" * 32
        status, _, body = request(port, path, extra_headers={"Cookie": f"sessionid={session}; csrftoken=" + "a" * 32})
        token = re.search(rb'name="csrfmiddlewaretoken" value="([A-Za-z0-9]{64})"', body)
        with patch("django.middleware.csrf._get_new_csrf_string", return_value=token[1][:32].decode() if token else "a" * 32):
            reference = client.get(path)
        assert status == reference.status_code == 200, (path, status)
        # The native revisions use a different digest and a ballot rejects stale saves.
        if path.startswith("/outround_pairing/") and "/pair/" not in path:
            body = re.sub(rb' data-revision="[a-f0-9]{64}"', b"", body)
        elif "/result/" in path:
            body = re.sub(rb'<input type="hidden" name="revision" value="[a-f0-9]+">', b"", body)
        elif path == "/apda-board/outrounds/":
            body = re.sub(rb'name="revision" value="[a-zA-Z0-9]+"', b'name="revision" value="<revision>"', body)
            reference.content = re.sub(rb'name="revision" value="[a-zA-Z0-9]+"', b'name="revision" value="<revision>"', reference.content)
        assert_html_bytes(body, reference.content)
        return body

    with rust_server(overrides=html_assets) as port:
        for path in paths:
            with subtests.test(path=path):
                compare(port, path, staff)
                assert request(port, path, cookie=board)[1]["Location"] == "/403/"

        board_path = "/apda-board/outrounds/"
        with subtests.test(path=board_path):
            shown = compare(port, board_path, board)
            assert b"Private staff-only note" not in shown and b"Absent Judge" not in shown
            assert b"7.25" not in shown and b"Requests Wing" not in shown
            assert b"Casey &amp; Co" in shown and b"manual edits" in shown
        with subtests.test(path=board_path, protected_edit=True):
            # Change protected data while leaving every public pairing field alone.
            _, _, before = request(port, board_path, extra_headers={"Cookie": f"sessionid={board}; csrftoken=" + "a" * 32})
            Judge.objects.filter(pk=chair.id).update(rank=9.12, wing_only=True, request_wing=False, no_wings=True)
            Scratch.objects.create(judge=unavailable, team=teams[0], scratch_type=Scratch.TAB_SCRATCH)
            _, _, after = request(port, board_path, extra_headers={"Cookie": f"sessionid={board}; csrftoken=" + "a" * 32})
            strip_token = lambda body: re.sub(rb'name="csrfmiddlewaretoken" value="[A-Za-z0-9]{64}"', b'name="csrfmiddlewaretoken"', body)
            assert strip_token(before) == strip_token(after)

        with subtests.test(case="stale staff correction"):
            csrf = "a" * 32
            headers = {"Cookie": f"sessionid={staff}; csrftoken={csrf}", "X-CSRFToken": csrf}
            path = "/outround_pairing/0/4"
            revision = re.search(
                rb'data-revision="([a-f0-9]{64})"[^>]*>\s*<div[^>]*>\s*<div class="row" round-id="'
                + str(var.id).encode() + rb'"',
                request(port, path, extra_headers=headers)[2],
            )[1].decode()

            def correction(path, revision):
                return request(port, path, method="POST", extra_headers=headers, form={"revision": revision})

            room_path = f"/outround/{var.id}/assign_room/{second_room.id}/"
            team_path = f"/outround/pairings/assign_team/{var.id}/gov/{teams[4].id}/"
            status, _, body = correction(room_path, revision)
            assert status == 200 and (updated := json.loads(body)["revision"]) != revision
            assert correction(team_path, revision)[0] == 422
            assert correction(f"/outround/{var.id}/switch_sides/", revision)[0] == 422
            assert correction(f"/outround_choice/{var.id}", revision)[0] == 422
            var.refresh_from_db()
            assert (var.gov_team_id, var.room_id, var.choice) == (teams[0].id, second_room.id, Outround.GOV)
            status, _, body = correction(team_path, updated)
            assert status == 200 and json.loads(body)["revision"] != updated
            var.refresh_from_db()
            assert var.gov_team_id == teams[4].id

        # Challenge entry before the opening varsity round, with and without a saved pick.
        Outround.objects.filter(type_of_round=0).delete()
        BreakingTeam.objects.filter(team=teams[4]).delete()
        TabSettings.set("outround_pairing_mode", 1)
        TabSettings.set("challenge_pick_generation_0", BreakingTeam.objects.filter(type_of_team=0).order_by("id").first().id)
        path = "/outround_pairing/pair/0/8/"
        for case in ("waiting", "picked", "checks"):
            with subtests.test(path=path, case=case):
                if case == "picked":
                    OutroundChallenge.objects.create(type_of_round=0, num_teams=4, challenger=teams[0], challenged=teams[2])
                compare(port, path + ("?stage=pairing" if case == "checks" else ""), staff)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("imported", (False, True))
def test_native_elimination_challenge_once_enforces_pool_and_seeds(tmp_path, imported):
    from mittab.apps.tab.models import OutroundChallenge

    tab = legacy_tab_session("challenge-staff")
    board = legacy_tab_session("challenge-board")
    get_user_model().objects.get(username="challenge-board").groups.add(Group.objects.create(name="APDA Board"))
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
        assert b"Enter Challenge Outs" in request(port, "/outround_pairing/0/4", cookie=tab)[2]
        path = "/outround_pairing/pair/0/8/"
        seeds = list(BreakingTeam.objects.order_by("seed"))
        a, b, c, d = [seed.team_id for seed in seeds]
        assert b"Recorded matchups" in request(port, path, cookie=tab)[2]
        action = {"action": "set_challenge", "challenger_id": a, "challenged_id": c}
        assert post(path, {**action, "challenged_id": a})[0] == 422
        assert post(path, action)[0] == 302
        assert post(path, {**action, "challenger_id": b})[0] == 422
        assert OutroundChallenge.objects.count() == 1
        assert post(path, {"action": "delete_challenge", "challenger_id": a})[0] == 302
        assert not OutroundChallenge.objects.exists()
        assert post(path, action)[0] == 302
        before_rebreak = list(BreakingTeam.objects.order_by("pk").values_list("id", "team_id", "seed"))
        unconfirmed_break = post("/break/")
        assert unconfirmed_break[0] == 422 and b'name="confirm"' in unconfirmed_break[2]
        assert list(BreakingTeam.objects.order_by("pk").values_list("id", "team_id", "seed")) == before_rebreak
        assert OutroundChallenge.objects.filter(challenger_id=a, challenged_id=c).exists()
        assert not Outround.objects.exists()
        result = post(path)
        assert result[0] == 302 and result[1]["Location"] == "/outround_pairing/0/4", result
        assert {frozenset((row.gov_team_id, row.opp_team_id)) for row in Outround.objects.all()} == {
            frozenset((a, c)), frozenset((b, d)),
        }
        assert BreakingTeam.objects.get(team_id=c).effective_seed == 4
        assert TabSettings.get(f"challenge_once_applied_0_{min(seed.id for seed in seeds)}", use_cache=False) == 1
        assert post(path, action)[0] == 422
        if imported:
            TabSettings.objects.filter(key__startswith="rust_seed_").delete()
        judge = Judge.objects.create(name="Challenge protected judge", rank=9, wing_only=True)
        matching = list(Outround.objects.order_by("id"))
        for row in matching:
            row.judges.add(judge)
            Outround.objects.filter(id=row.id).update(chair=judge)
        assigned = {row.id: list(row.judges.values_list("id", flat=True)) for row in matching}
        challenged_match = next(row for row in matching if c in (row.gov_team_id, row.opp_team_id))
        ballot = f"/outround/{challenged_match.id}/result/"
        for winning_team in (c, a, c):
            code = Outround.GOV if winning_team == challenged_match.gov_team_id else Outround.OPP
            assert post(ballot, {"winner": code, "round_instance": challenged_match.id})[0] == 302
        assert BreakingTeam.objects.get(team_id=c).effective_seed == 1
        assert b"manual edits" in request(port, "/apda-board/outrounds/", cookie=board)[2]
        preview = request(port, "/outround_pairing/0/4", cookie=tab)[2]
        assert b"GOV win" in preview or b"OPP win" in preview
        assert any(event.get("public", {}).get("source") == "staff outround result"
                   for event in AuditEvent.objects.filter(object_id=challenged_match.id, content_type__model="outround")
                   .values_list("changes", flat=True))

        def board_post(url, form):
            return request(port, url, method="POST",
                           extra_headers={"Cookie": f"sessionid={board}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": csrf, **form})

        for row, winning_team in ((next(row for row in matching if c in (row.gov_team_id, row.opp_team_id)), c),
                                  (next(row for row in matching if b in (row.gov_team_id, row.opp_team_id)), b)):
            target = f"/apda-board/outround/{row.id}/"
            def revision():
                body = request(port, "/apda-board/outrounds/", cookie=board)[2].decode()
                return re.search(rf'action="{target}"[^>]*>\s*<input[^>]*>\s*<input type="hidden" name="revision" value="([^"]+)"', body)[1]

            assert board_post(target, {"action": "choice", "choice": 1, "revision": revision()})[0] == 302
            result = board_post(target, {"action": "result", "victor": 1 if winning_team == row.gov_team_id else 2,
                                         "revision": revision()})
            assert result[0] == 302, result
        assert BreakingTeam.objects.get(team_id=c).effective_seed == 1
        assert BreakingTeam.objects.get(team_id=b).effective_seed == 2
        paired = board_post("/apda-board/outrounds/pair/", {"division": 0})
        assert paired[0] == 302, paired
        final = Outround.objects.get(num_teams=2)
        assert {final.gov_team_id, final.opp_team_id} == {b, c}
        assert not final.judges.exists() and final.chair_id is None
        for row in matching:
            row.refresh_from_db()
            assert row.chair_id == judge.id
            assert list(row.judges.values_list("id", flat=True)) == assigned[row.id]


@pytest.mark.django_db(transaction=True)
def test_native_backup_restore_is_atomic_and_preserves_scratches(tmp_path):
    from urllib.parse import quote
    from mittab.apps.tab.models import PublishedOutroundStage

    staff = legacy_tab_session("backup-staff")
    board = legacy_tab_session("backup-board")
    get_user_model().objects.get(username="backup-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="Backup example school")
    team = Team.objects.create(name="Backup's example team", school=school, seed=0)
    opponent = Team.objects.create(name="Backup other team", school=school, seed=0)
    judge = Judge.objects.create(name="Backup example judge", rank=5)
    scratch = Scratch.objects.create(judge=judge, team=team, scratch_type=Scratch.TAB_SCRATCH)
    Round.objects.create(round_number=1, gov_team=team, opp_team=opponent, victor=Round.GOV)
    listing = "/pairing/view_backups/"
    creation = "/pairing/backup/"
    with rust_server(overrides={"BACKUP_PREFIX": str(tmp_path), "BACKUP_STORAGE": ""}) as port:
        status, headers, body = request(port, listing, cookie=staff)
        assert status == 200 and b"No backups found" in body, body
        csrf = load_cookies(headers)["csrftoken"].value
        cookie = f"sessionid={staff}; csrftoken={csrf}"

        def post(path, fields):
            return request(port, path, method="POST", extra_headers={"Cookie": cookie},
                           form={"csrfmiddlewaretoken": csrf, **fields})

        # A template GET in a second tab must leave an open form's old Rust token usable.
        old_csrf = "A" * 43
        old_cookie = {"Cookie": f"sessionid={staff}; csrftoken={old_csrf}"}
        status, old_headers, page = request(port, listing, extra_headers=old_cookie)
        assert status == 200 and b'value="' + old_csrf.encode() + b'"' in page
        assert not old_headers.get("Set-Cookie")
        assert request(port, creation, method="POST", extra_headers=old_cookie,
                       form={"csrfmiddlewaretoken": old_csrf, "backup_name": "Legacy CSRF"})[0] == 302
        legacy_backup, = tmp_path.glob("Legacy CSRF*.dump.sql")
        legacy_backup.unlink()

        assert request(port, creation, cookie=staff)[0] == 302
        assert not list(tmp_path.iterdir())
        assert post(creation, {"backup_name": "é" * 127})[0] == 422
        assert not list(tmp_path.iterdir())
        assert post(creation, {"backup_name": "é" * 90})[0] == 302
        long_backup, = tmp_path.glob("é*.dump.sql")
        long_backup.unlink()
        assert request(port, creation, method="POST", cookie=staff, form={"backup_name": "CSRF"})[0] == 403
        assert post(creation, {"backup_name": "Round + example"})[0] == 302
        clean, = tmp_path.glob("Round + example*.dump.sql")
        assert clean.stat().st_mode & 0o777 == 0o600
        data = clean.read_bytes()
        assert b"CREATE TABLE `tab_scratch`" not in data
        key = quote(clean.name.removesuffix(".dump.sql"), safe="")
        download = f"/backup/download/{key}/"
        restore = f"/backup/restore/{key}/"
        assert clean.name.removesuffix(".dump.sql").encode() in request(port, listing, cookie=staff)[2]
        assert request(port, f"/backup/{key}", cookie=staff)[0] == 200
        status, headers, body = request(port, download, cookie=staff)
        assert status == 200 and body == data
        assert headers["Content-Type"] == "application/sql"
        assert key in headers["Content-Disposition"]
        for path in (listing, f"/backup/{key}", download, restore, "/upload_backup/"):
            assert request(port, path, cookie=board)[1]["Location"] == "/403/"

        assert post(creation, {"backup_name": "Full", "include_scratches": "on"})[0] == 302
        full, = tmp_path.glob("Full*.dump.sql")
        assert b"CREATE TABLE `tab_scratch`" in full.read_bytes()
        assert b"Restore Backup" in request(port, restore, cookie=staff)[2]
        assert post(restore, {})[0] == 422

        bad = tmp_path / "Invalid_7_2_3_U.dump.sql"
        dump, footer = full.read_bytes().rsplit(b"-- Dump completed on ", 1)
        bad.write_bytes(dump + b"INSERT INTO `tab_team` (`id`,`absent_column`) VALUES (10000,0);\n"
                        + b"-- Dump completed on " + footer)
        Judge.objects.filter(id=judge.id).update(rank=9)
        response = post("/backup/restore/Invalid_7_2_3_U/", {"confirm": "on"})
        assert response[0] == 422 and b"incompatible with the installed" in response[2], response
        judge.refresh_from_db()
        assert judge.rank == 9 and Scratch.objects.filter(id=scratch.id).exists()
        assert multipart_request(port, "/upload_backup/", fields={"csrfmiddlewaretoken": csrf},
                                 file=("bad.sql", b"not a backup"), cookie=cookie)[0] == 422
        assert multipart_request(port, "/upload_backup/", fields={"csrfmiddlewaretoken": csrf},
                                 file=("example.sql", data), cookie=cookie)[0] == 302
        uploaded, = tmp_path.glob("example_7*.dump.sql")
        assert uploaded.read_bytes() == data

        later = Team.objects.create(name="Added after backup", school=school, seed=0)
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE review_restore_extension (team_id INT, FOREIGN KEY (team_id) REFERENCES tab_team(id)) ENGINE=InnoDB")
            cursor.execute("INSERT INTO review_restore_extension VALUES (%s)", [later.id])
        try:
            response = post(restore, {"confirm": "on"})
            assert response[0] == 422 and b"reference to a missing tournament record" in response[2]
            assert Team.objects.filter(pk=later.id).exists()
        finally:
            with connection.cursor() as cursor:
                cursor.execute("DROP TABLE review_restore_extension")

        stage = PublishedOutroundStage.objects.create(type_of_round=0, num_teams=2)
        PublishedTeamPlacement.objects.create(stage=stage, team=team, place=1)
        # An older mysqldump has a valid footer, without the later placement tables.
        before_publication = re.sub(
            rb"(?ms)^-- Table structure for table `tab_published(?:teamplacement|outroundstage)`\r?\n"
            rb".*?(?=^-- Table structure for table |^-- Dump completed on )", b"", data,
        )
        assert b"CREATE TABLE `tab_publishedoutroundstage`" not in before_publication
        assert b"CREATE TABLE `tab_publishedteamplacement`" not in before_publication
        clean.write_bytes(before_publication)
        response = post(restore, {"confirm": "on"})
        assert response[0] == 302 and response[1]["Location"] == "/public/login/", response
        assert not PublishedTeamPlacement.objects.exists()
        assert not PublishedOutroundStage.objects.exists()
        judge.refresh_from_db()
        assert judge.rank == 5
        assert Scratch.objects.filter(id=scratch.id, team_id=team.id, judge_id=judge.id).exists()
        assert len(list(tmp_path.glob("Before Restoring Backup*.dump.sql"))) == 3
        assert request(port, listing, cookie=staff)[1]["Location"].startswith("/public/login/")


@contextmanager
def mock_backup_objects():
    from html import escape
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import parse_qs, unquote, urlsplit

    objects = {}
    received = []

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, body=b""):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def signed(self, data=b""):
            auth = self.headers.get("Authorization", "")
            return ("Credential=fixture-access/" in auth and "/s3/aws4_request" in auth
                    and self.headers.get("x-amz-content-sha256") == sha256(data).hexdigest())

        def do_PUT(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            path = unquote(self.path)
            received.append((self.command, path))
            if not self.signed(body) or self.headers.get("If-None-Match") != "*":
                return self.respond(400)
            if path in objects:
                return self.respond(412)
            objects[path] = body
            return self.respond(200)

        def do_GET(self):
            uri = urlsplit(self.path)
            path = unquote(uri.path)
            received.append((self.command, path))
            if not self.signed():
                return self.respond(400)
            if path == "/fixture-bucket":
                params = parse_qs(uri.query)
                if params.get("prefix") != ["example/backups/"] or params.get("list-type") != ["2"]:
                    return self.respond(400)
                keys = sorted(key.removeprefix("/fixture-bucket/") for key in objects)
                if params.get("continuation-token") == ["next/+"]:
                    contents = keys[1:]
                    cursor = "<IsTruncated>false</IsTruncated>"
                else:
                    contents = keys[:1]
                    cursor = "<IsTruncated>true</IsTruncated><NextContinuationToken>next/+</NextContinuationToken>"
                xml = ("<ListBucketResult>" + cursor
                       + "".join(f"<Contents><Key>{escape(item)}</Key></Contents>" for item in contents)
                       + "</ListBucketResult>")
                return self.respond(200, xml.encode())
            return self.respond(200, objects[path]) if path in objects else self.respond(404)

    with ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", objects, received
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.django_db(transaction=True)
def test_native_signed_s3_backups_page_through_objects():
    from urllib.parse import quote

    staff = legacy_tab_session("backup-object-staff")
    TabSettings.set("cur_round", 1)
    listing = "/pairing/view_backups/"
    with mock_backup_objects() as (endpoint, objects, received), rust_server(overrides={
        "BACKUP_STORAGE": "S3", "BACKUP_PREFIX": "example/backups", "BACKUP_BUCKET": "fixture-bucket",
        "BACKUP_S3_ENDPOINT": endpoint, "AWS_ACCESS_KEY_ID": "fixture-access",
        "AWS_SECRET_ACCESS_KEY": "fixture-secret", "BACKUP_S3_REGION": "fixture-1",
    }) as port:
        status, headers, body = request(port, listing, cookie=staff)
        assert status == 200 and b"No backups found" in body, body
        csrf = load_cookies(headers)["csrftoken"].value
        for name in ("First & backup", "Second backup"):
            result = request(port, "/pairing/backup/", method="POST",
                             extra_headers={"Cookie": f"sessionid={staff}; csrftoken={csrf}"},
                             form={"csrfmiddlewaretoken": csrf, "backup_name": name})
            assert result[0] == 302, result
        assert len(objects) == 2
        assert all(key.startswith("/fixture-bucket/example/backups/") for key in objects)
        status, _, body = request(port, listing, cookie=staff)
        assert status == 200 and b"First &amp; backup" in body and b"Second backup" in body, body
        assert sum(path == "/fixture-bucket" for _, path in received) == 4
        for key, data in objects.items():
            name = quote(key.rsplit("/", 1)[-1].removesuffix(".dump.sql"), safe="")
            status, _, raw = request(port, f"/backup/download/{name}/", cookie=staff)
            assert status == 200 and raw == data and b"tab_tabsettings" in raw


@pytest.mark.django_db(transaction=True)
def test_native_roster_groups_change_members_atomically():
    from mittab.apps.tab.models import RankingGroup

    staff = legacy_tab_session("roster-group-staff")
    board = legacy_tab_session("roster-group-board")
    get_user_model().objects.get(username="roster-group-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="Group School")
    teams = [Team.objects.create(name=f"Group Team {i}", school=school, seed=0) for i in (1, 2)]
    debater = Debater.objects.create(name="Group Debater", novice_status=0)
    judge = Judge.objects.create(name="Group Judge", rank=3)
    room = Room.objects.create(name="Group Room", rank=2)
    Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1], victor=Round.GOV)
    with rust_server() as port:
        status, headers, body = request(port, "/manage-room-tags", cookie=staff)
        assert status == 200 and b"Create Room Tag" in body
        csrf = load_cookies(headers)["csrftoken"].value

        def post(path, fields, session=staff):
            return request(port, path, method="POST",
                           extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": csrf, **fields})

        fields = {"tag": "Accessibility", "priority": "75.25", "teams": teams[0].id,
                  "judges": judge.id, "rooms": room.id}
        assert post("/room-tag/", fields, board)[1]["Location"] == "/403/"
        assert post("/room-tag/", {**fields, "priority": "100"})[0] == 422
        assert post("/room-tag/", fields)[0] == 302
        tag = RoomTag.objects.get()
        assert list(tag.team_set.all()) == teams[:1]
        assert list(tag.judge_set.all()) == [judge] and list(tag.room_set.all()) == [room]
        uri = f"/room-tag/{tag.id}/"
        assert b"Group Judge" in request(port, uri, cookie=staff)[2]
        assert post(uri, {**fields, "priority": "98", "teams": 9999999})[0] == 422
        tag.refresh_from_db()
        assert tag.priority == Decimal("75.25") and list(tag.team_set.all()) == teams[:1]
        assert post(uri, {**fields, "teams": teams[1].id})[0] == 302
        assert list(tag.team_set.all()) == teams[1:]

        group_fields = {"name": "Regional", "teams": teams[1].id, "debaters": debater.id}
        assert post("/ranking-group/", group_fields)[0] == 302
        assert post("/ranking-group/", group_fields)[0] == 422
        group = RankingGroup.objects.get()
        assert list(group.teams.all()) == teams[1:] and list(group.debaters.all()) == [debater]
        group_url = f"/ranking-group/{group.id}/"
        assert request(port, group_url, cookie=staff)[0] == 200
        assert request(port, group_url, cookie=board)[1]["Location"] == "/403/"
        assert post(group_url, {"_method": "DELETE"})[0] == 302
        assert post(uri, {"_method": "DELETE"})[0] == 302
        assert not RankingGroup.objects.exists() and not RoomTag.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_native_raw_archive_matches_legacy_xml_and_is_staff_only():
    from xml.etree.ElementTree import fromstring, tostring

    from mittab.apps.tab.archive import ArchiveExporter

    staff = legacy_tab_session("archive-staff")
    board = legacy_tab_session("archive-board")
    get_user_model().objects.get(username="archive-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="École & Company")
    other = School.objects.create(name="Alternate School")
    names = ('Home "Team"', "Away & Team")
    teams = [Team.objects.create(name=name, school=school, hybrid_school=other, seed=0,
                                  team_code=f"fixture-code-{index}")
             for index, name in enumerate(names)]
    judge = Judge.objects.create(name="Private rank & Judge", rank=Decimal("5.25"))
    judge.schools.add(school, other)
    room = Room.objects.create(name="Room < 3", rank=1)
    played = Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1], victor=Round.GOV,
                                  room=room, chair=judge)
    played.judges.add(judge)
    for index, role in enumerate(("pm", "lo", "mg", "mo")):
        debater = Debater.objects.create(name=f"Test speaker {index} œ", novice_status=index % 2)
        teams[index % 2].debaters.add(debater)
        RoundStats.objects.create(round=played, debater=debater, speaks=Decimal("26.25")+index,
                                  ranks=index+1, debater_role=role)
    uri = "/archive/download/"
    with rust_server() as port:
        status, headers, body = request(port, uri, cookie=staff, extra_headers={"Host": "fixture.example.test"})
        assert status == 200, body
        assert headers["Content-Disposition"] == "attachment; filename=fixture.xml"
        assert headers["Cache-Control"] == "private, no-store"
        expected = fromstring(ArchiveExporter("fixture").export_tournament())
        actual = fromstring(body)
        assert tostring(actual) == tostring(expected)
        assert request(port, uri, cookie=board)[1]["Location"] == "/403/"


@pytest.mark.django_db(transaction=True)
def test_native_roster_log_management_uses_fixed_est_permissions_and_privacy():
    from django.contrib.auth.models import Permission

    staff = legacy_tab_session("log-admin")
    board = legacy_tab_session("log-board")
    get_user_model().objects.get(username="log-board").groups.add(Group.objects.create(name="APDA Board"))
    editor = get_user_model().objects.create_user("log-editor", password="fixture-editor-password", is_staff=True)
    editor.user_permissions.add(Permission.objects.get(codename="change_publicrosterchange"))
    editor_client = Client()
    editor_client.force_login(editor, backend="mittab.apps.tab.auth_backends.TabAuthenticationBackend")
    editor_session = editor_client.cookies["sessionid"].value
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    TabSettings.set("teams_public", 1)
    TabSettings.set("judges_public", 1)
    TabSettings.set("debaters_public", 0)
    school = School.objects.create(name="Log Test")
    teams = [Team.objects.create(name=f"Log Team {i}", school=school, seed=0) for i in (1, 2)]
    Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1], victor=Round.GOV)
    entry = PublicRosterChange.objects.create(list_kind="teams", message="Edited public label")
    PublicRosterChange.objects.bulk_create([
        PublicRosterChange(list_kind="judges", message=f"Fixture availability {i}") for i in range(34)
    ])
    base = "/setup/public-change-log/"
    uri = f"{base}{entry.id}/"
    with rust_server() as port:
        status, _, body = request(port, f"{base}?kind=judges&page=2", cookie=staff)
        assert status == 200 and b"Page 2 of 2" in body and b"Edited public label" not in body
        assert request(port, base, cookie=board)[1]["Location"] == "/403/"
        assert request(port, uri, cookie=board)[1]["Location"] == "/403/"
        status, headers, body = request(port, uri, cookie=editor_session)
        assert status == 200 and b"Edited public label" in body and b"Delete Entry" not in body
        csrf = load_cookies(headers)["csrftoken"].value

        def post(session, **fields):
            return request(port, uri, method="POST", extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": csrf, **fields})

        assert post(editor_session, action="delete")[0] == 403
        fields = {"list_kind": "judges", "message": "Corrected private debater label",
                  "occurred_at": "2026-07-01T12:10", "involves_debaters": "on"}
        assert post(board, **fields)[1]["Location"] == "/403/"
        assert post(editor_session, **fields)[0] == 302
        entry.refresh_from_db()
        assert entry.created_at.isoformat() == "2026-07-01T17:10:00+00:00"
        assert entry.involves_debaters
        assert b"Corrected private debater label" not in request(port, "/public/judges/")[2]
        assert post(staff, action="delete")[0] == 302
        assert not PublicRosterChange.objects.filter(id=entry.id).exists()


@pytest.mark.django_db(transaction=True)
def test_native_staff_rankings_and_report_match_reference():
    from mittab.apps.tab.judge_speaker_points import judge_speaker_metrics
    from mittab.apps.tab.models import RankingGroup
    from mittab.apps.tab.views.debater_views import get_speaker_rankings
    from mittab.libs.tab_logic.rankings import get_team_rankings

    staff = legacy_tab_session("ranking-staff")
    board = legacy_tab_session("ranking-board")
    get_user_model().objects.get(username="ranking-board").groups.add(Group.objects.create(name="APDA Board"))
    for key, value in {"cur_round": 4, "tot_rounds": 3, "var_teams_to_break": 2,
                       "nov_teams_to_break": 0, "speaker_rankings_use_single_adjusted": 1}.items():
        TabSettings.set(key, value)
    school = School.objects.create(name="Rank School")
    teams = [Team.objects.create(name=f"Rank Team {i}", school=school, seed=0) for i in (1, 2)]
    speakers = [Debater.objects.create(name=f"Rank Person {i}", novice_status=i % 2) for i in range(4)]
    for i, speaker in enumerate(speakers):
        teams[i % 2].debaters.add(speaker)
    judges = [Judge.objects.create(name=f"Rank Judge {i}", rank=3, is_dino=i == 0) for i in range(3)]
    group = RankingGroup.objects.create(name="Regional Fixture")
    group.teams.add(teams[1])
    group.debaters.add(speakers[0], speakers[3])
    for number in range(1, 4):
        played = Round.objects.create(round_number=number, gov_team=teams[0], opp_team=teams[1],
                                      victor=Round.GOV if number != 3 else Round.OPP)
        played.judges.add(judges[0], judges[number % 2 + 1])
        for i, role in enumerate(("pm", "lo", "mg", "mo")):
            RoundStats.objects.create(round=played, debater=speakers[i], speaks=Decimal("25.2557") + i + number,
                                      ranks=i+1, debater_role=role)
    judges_url = "/judges/speaker-points/"
    with rust_server() as port:
        for url in ("/team/ranking/", "/team/rank/", "/debater/ranking/", "/debater/rank/", judges_url):
            assert request(port, url, cookie=board)[1]["Location"] == "/403/"
        status, _, team_ranks = request(port, "/team/rank/", cookie=staff)
        assert status == 200 and b"Regional Fixture Rankings" in team_ranks, team_ranks
        expected_teams, _ = get_team_rankings(None)
        locations = [team_ranks.index(f"/team/{row[0].id}/".encode()) for row in expected_teams]
        assert locations == sorted(locations)
        status, _, speakers_result = request(port, "/debater/rank/", cookie=staff)
        assert status == 200 and b"Regional Fixture Rankings" in speakers_result, speakers_result
        assert speakers_result.index(b"Single adjusted speaks") < speakers_result.index(b"Unadjusted speaks")
        expected_speakers, _ = get_speaker_rankings()
        locations = [speakers_result.index(f"/debater/{row[0].id}/".encode()) for row in expected_speakers]
        assert locations == sorted(locations)
        status, _, preview = request(port, "/forum_post", cookie=staff)
        assert status == 200, preview
        for value in (b"Rank Judge 0", b"2 teams", b"2 novices", b"Novice Speaker Awards", b"Varsity Breaking Teams"):
            assert value in preview
        status, headers, board_preview = request(port, "/forum_post", cookie=board)
        assert status == 200 and headers["Cache-Control"] == "private, no-store"
        assert b"Varsity Breaking Teams" in board_preview and b"Rank Judge 0" in board_preview
        assert request(port, "/forum_post", method="HEAD", cookie=board)[0] == 200
        assert request(port, "/forum_post", method="POST", cookie=board)[1]["Location"] == "/403/"
        Scratch.objects.create(team=teams[0], judge=judges[1], scratch_type=Scratch.TAB_SCRATCH)
        Judge.objects.filter(pk=judges[1].pk).update(rank=9, wing_only=True)
        assert request(port, "/forum_post", cookie=board)[2] == board_preview
        highlighted = judges[2]
        status, headers, report = request(port, f"{judges_url}?minimum_rounds=1&judge={highlighted.id}", cookie=staff)
        assert status == 200 and headers["Cache-Control"] == "private, no-store"
        report = report.decode()
        assert report.count('aria-label="Scatter plot') == 2
        for row in judge_speaker_metrics(1):
            attributes, cells = re.search(rf'<tr data-judge-id="{row["id"]}"([^>]*)>(.*?)</tr>',
                                          report, re.DOTALL).groups()
            assert ("speaker-points-highlight" in attributes) == (row["id"] == highlighted.id)
            assert row["name"] in cells and f'{row["mean"]:.2f} ' in cells and f'#{row["mean_rank"]}' in cells
            assert f'{row["generosity"]:.2f} ' in cells and f'#{row["generosity_rank"]}' in cells
        assert f'data-judge-id="{highlighted.id}"'.encode() not in request(port, judges_url, cookie=staff)[2]


@pytest.mark.django_db(transaction=True)
def test_native_new_tournament_requires_confirmation_and_saves_previous_data(tmp_path):
    from mittab.apps.registration.models import Registration

    staff = legacy_tab_session("reset-staff")
    board = legacy_tab_session("reset-board")
    get_user_model().objects.get(username="reset-board").groups.add(Group.objects.create(name="APDA Board"))
    for key, value in {"cur_round": 2, "tot_rounds": 1, "tournament_name": "Before Reset",
                       "teams_public": 1, "judges_public": 1}.items():
        TabSettings.set(key, value)
    school = School.objects.create(name="Reset School")
    registration = Registration.objects.create(school=school, email="reset@example.invalid")
    teams = [Team.objects.create(name=f"Reset Team {i}", school=school, seed=0, registration=registration)
             for i in (1, 2)]
    Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1], victor=Round.GOV)
    config = RegistrationConfig.get_or_create_active()
    config.allow_new_registrations = True
    config.save()
    PublicRosterChange.objects.create(list_kind="teams", message="Before reset public history")
    confirm = "/pairing/confirm_start_tourny/"
    reset = "/pairing/start_tourny/"
    assert rust_command("migrate", connection.settings_dict["NAME"]).returncode == 0
    with rust_server(overrides={"BACKUP_PREFIX": str(tmp_path), "BACKUP_STORAGE": ""}) as port:
        status, headers, body = request(port, confirm, cookie=staff)
        assert status == 200 and b"Before Reset" in body, body
        assert request(port, reset, cookie=staff)[0] == 200
        assert request(port, confirm, cookie=board)[1]["Location"] == "/403/"
        assert Team.objects.count() == 2 and not list(tmp_path.iterdir())
        csrf = load_cookies(headers)["csrftoken"].value

        def post(fields, session=staff):
            return request(port, reset, method="POST",
                           extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": csrf, **fields})

        action = {"revision": "initial", "confirm": "on"}
        assert post(action, board)[1]["Location"] == "/403/"
        assert post({"revision": "initial"})[0] == 422
        assert post({**action, "revision": "stale"})[0] == 409
        result = post(action)
        assert result[0] == 302 and result[1]["Location"] == "/setup/start-tournament/", result
        assert not Team.objects.exists() and not Round.objects.exists()
        assert not Registration.objects.exists() and not PublicRosterChange.objects.exists()
        assert get_user_model().objects.filter(username="reset-staff").exists()
        assert TabSettings.get("cur_round", use_cache=False) == 1
        assert TabSettings.get("tournament_name", use_cache=False) == "New Tournament"
        assert not RegistrationConfig.get_active().allow_new_registrations
        before, = tmp_path.glob("Before New Tournament*.dump.sql")
        assert b"Before reset public history" in before.read_bytes()
        assert b"Reset Team" in before.read_bytes()
        assert request(port, "/setup/start-tournament/", cookie=staff)[0] == 200


@pytest.mark.django_db(transaction=True)
def test_native_simulation_is_dev_only_and_adds_supported_ironman_ballots():
    staff = legacy_tab_session("simulation-staff")
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 2)
    school = School.objects.create(name="Simulation School")
    teams = [Team.objects.create(name=f"Simulation Team {i}", school=school, seed=0) for i in (1, 2)]
    for i, count in enumerate((1, 2)):
        for j in range(count):
            teams[i].debaters.add(Debater.objects.create(name=f"Simulated Speaker {i}-{j}", novice_status=0))
    debate = Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1])
    future = Round.objects.create(round_number=2, gov_team=teams[1], opp_team=teams[0])
    url = "/pairings/simulate_rounds/"
    for scope, expected in (("production", 422), ("development", 302)):
        with rust_server(overrides={"MITTAB_ENV": scope}) as port:
            status, headers, _ = request(port, url, cookie=staff)
            assert status == 200
            assert not RoundStats.objects.exists()
            csrf = load_cookies(headers)["csrftoken"].value
            result = request(port, url, method="POST",
                             extra_headers={"Cookie": f"sessionid={staff}; csrftoken={csrf}"},
                             form={"csrfmiddlewaretoken": csrf})
            assert result[0] == expected, result
    debate.refresh_from_db()
    future.refresh_from_db()
    assert debate.victor in (Round.GOV, Round.OPP) and future.victor == Round.NONE
    stats = {row.debater_role: row for row in RoundStats.objects.filter(round=debate)}
    assert set(stats) == {"pm", "mg", "lo", "mo"}
    assert stats["pm"].debater_id == stats["mg"].debater_id
    assert stats["lo"].debater_id != stats["mo"].debater_id
    assert sorted(row.ranks for row in stats.values()) == [1, 2, 3, 4]


@pytest.mark.django_db(transaction=True)
def test_native_test_deployment_seeds_fixture_without_overwriting_credentials():
    from django.contrib.auth.hashers import check_password
    from uuid import uuid4

    existing = connection.settings_dict["NAME"]
    database = f"{existing[:43]}_fixture_{uuid4().hex[:8]}"
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
    try:
        result = rust_command("init", database, tab_password="fixture-auth-password",
                              overrides={"TOURNAMENT_NAME": "synthetic-test"})
        assert result.returncode == 0, result.stderr
        with connection.cursor() as cursor:
            for table, count in (("tab_school", 43), ("tab_team", 157), ("tab_judge", 130),
                                 ("tab_scratch", 697), ("tab_roomcheckin", 700)):
                cursor.execute(f"SELECT COUNT(*) FROM `{database}`.`{table}`")
                assert cursor.fetchone()[0] == count
            cursor.execute(f"SELECT password FROM `{database}`.auth_user WHERE username = 'tab'")
            assert check_password("fixture-auth-password", cursor.fetchone()[0])
        assert rust_command("init", database).returncode == 0
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE `{database}`")


@pytest.mark.django_db(transaction=True)
def test_default_admin_model_lists_match_django_bytes(html_assets):
    from mittab.apps.tab.models import (
        JudgeCodeEmailLog, OutroundChallenge, RankingGroup, UserTournamentSetupPreference, WrittenRFDEmailLog,
    )

    TabSettings.set("start_tournament_wizard_completed", 1)
    TabSettings.set("outround_pairing_mode", 1)
    TabSettings.set("admin-list-test", None)
    school = School.objects.create(name="Zebra & School")
    School.objects.create(name="A. Example")
    team = Team.objects.create(name="Zebra 'A'", school=school, seed=Team.UNSEEDED)
    second = Team.objects.create(name="Apex", school=school, seed=Team.UNSEEDED)
    Debater.objects.create(name="Debater & Example", novice_status=0)
    judge = Judge.objects.create(name="Judge <Example>", rank=5)
    room = Room.objects.create(name="Room Example", rank=2)
    RoomTag.objects.create(tag="Tag Example", priority=1)
    RankingGroup.objects.create(name="Zebra Ranking Group")
    debate = Round.objects.create(round_number=1, gov_team=team, opp_team=second)
    Outround.objects.create(num_teams=2, type_of_round=Outround.VARSITY, gov_team=team, opp_team=second)
    challenge = OutroundChallenge.objects.create(
        num_teams=2, type_of_round=Outround.VARSITY, challenger=team, challenged=second,
    )
    Scratch.objects.create(judge=judge, team=team, scratch_type=Scratch.TAB_SCRATCH)
    CheckIn.objects.create(judge=judge, round_number=1)
    RoomCheckIn.objects.create(room=room, round_number=0)
    Bye.objects.create(bye_team=team, round_number=2)
    NoShow.objects.create(no_show_team=second, round_number=1)
    BreakingTeam.objects.create(team=team, seed=1)
    JudgeCodeEmailLog.objects.create(judge=judge, email="fixture@example.invalid", ballot_code="fixture")
    WrittenRFDEmailLog.objects.create(round=debate, email="test@example.invalid")
    session = legacy_tab_session("model-list-parity")
    user = get_user_model().objects.get(username="model-list-parity")
    UserTournamentSetupPreference.objects.create(user=user, hide_tournament_todo=True)
    RoomTag.objects.bulk_create([RoomTag(tag=f"Tag {index}", priority=1) for index in range(101)])

    models = (
        "school", "debater", "team", "judge", "room", "roomtag", "rankinggroup", "round", "outround",
        "scratch", "checkin", "roomcheckin", "bye", "noshow", "breakingteam", "tabsettings",
        "usertournamentsetuppreference", "judgecodeemaillog", "writtenrfdemaillog", "outroundchallenge",
    )
    paths = [f"/admin/tab/{model}/" for model in models]
    paths += [
        "/admin/tab/roomtag/?p=2", "/admin/tab/roomtag/?all=", "/admin/tab/school/?_to_field=id&_popup=1",
        "/admin/tab/roomtag/?p=999", f"/admin/tab/team/?id__exact={team.id}",
        "/admin/tab/team/?id__exact=0", "/admin/tab/team/?id__exact=bad", "/admin/tab/team/?missing_field=1",
    ]
    client = Client()
    client.cookies["sessionid"] = session
    client.cookies["csrftoken"] = "a" * 32
    with rust_server(overrides=html_assets) as port:
        for path in paths:
            compare_admin_page(port, path, client)
        challenge_path = "/admin/tab/outroundchallenge/"
        detail = f"{challenge_path}{challenge.id}/change/"
        assert request(port, detail, cookie=session)[0] == 200
        TabSettings.set("outround_pairing_mode", 0)
        for path in (challenge_path, detail):
            assert client.get(path).status_code == 403
            assert request(port, path, cookie=session)[0] == 403


@pytest.mark.django_db(transaction=True)
def test_admin_assignment_and_audit_records_match_django_bytes(html_assets):
    from django.apps import apps
    from django.contrib.admin.models import CHANGE, LogEntry
    from django.contrib.contenttypes.models import ContentType
    from mittab.apps.tab.models import ManualJudgeAssignment

    type_names = Path(__file__).parent / "templates" / "django_admin" / "content_type_names.json"
    assert json.loads(type_names.read_text()) == {
        model._meta.label_lower: str(model._meta.verbose_name) for model in apps.get_models()
    }

    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    staff = legacy_tab_session("record-parity")
    user = get_user_model().objects.get(username="record-parity")
    board = legacy_tab_session("records-board")
    get_user_model().objects.get(username="records-board").groups.add(Group.objects.create(name="APDA Board"))
    school = School.objects.create(name="Admin Place")
    first = Team.objects.create(name="Company & West", school=school, seed=0)
    second = Team.objects.create(name="East Side", school=school, seed=0)
    debate = Round.objects.create(round_number=1, gov_team=first, opp_team=second, victor=Round.GOV)
    judge = Judge.objects.create(name="Z. Example", rank=3)
    other = Judge.objects.create(name="A. Example", rank=4)
    debate.judges.add(judge, other)
    row, _ = ManualJudgeAssignment.objects.update_or_create(round=debate, judge=judge, defaults={"created_by": user})
    later, _ = ManualJudgeAssignment.objects.update_or_create(round=debate, judge=other, defaults={"created_by": None})
    fixed = datetime(2025, 9, 7, 12, 0, tzinfo=timezone.get_default_timezone())
    ManualJudgeAssignment.objects.filter(pk=row.id).update(created_at=fixed)
    ManualJudgeAssignment.objects.filter(pk=later.id).update(created_at=fixed + timedelta(minutes=5))
    event = AuditEvent.record(debate, AuditEvent.MANUAL_JUDGE_ASSIGN, user, changes={
        "fields": ["judges", "éclair"], "reason": "fixture <evidence>", "a": {"vote": 1},
    }, note="Added by hand & checked.\nSecond line.")
    excluded = AuditEvent.record(judge, AuditEvent.EDIT, note="Earlier note")
    AuditEvent.objects.filter(pk=event.id).update(created_at=fixed)
    AuditEvent.objects.filter(pk=excluded.id).update(created_at=fixed - timedelta(days=1))
    LogEntry.objects.create(
        action_time=fixed, content_type=ContentType.objects.get_for_model(ManualJudgeAssignment),
        user=user, object_id=row.id, object_repr=str(row), action_flag=CHANGE,
        change_message='[{"changed": {"fields": ["Judge"]}}]',
    )
    assignments = "/admin/tab/manualjudgeassignment/"
    events = "/admin/tab/auditevent/"
    paths = [
        assignments, f"{assignments}?o=1", f"{assignments}?o=-0.1",
        f"{assignments}{row.id}/change/", f"{assignments}{later.id}/change/", f"{assignments}{row.id}/history/",
        events, f"{events}?event_type__exact={event.event_type}", f"{events}?content_type__id__exact={event.content_type_id}",
        f"{events}?q=Company+West", f"{events}?o=2&q=Added+hand", f"{events}?q=missing",
        f"{events}{event.id}/change/", f"{events}{excluded.id}/change/", f"{events}{event.id}/history/",
        f"{events}{event.id}/change/?_changelist_filters=q%3DCompany%2BWest",
        f"{events}{event.id}/change/?_popup=1&_to_field=id",
        f"{events}?q=%22Added+by+hand%22", f"{events}?id__exact={event.id}",
        f"{assignments}?id__exact={row.id}", f"{events}?q=+Added+hand+",
    ]
    client = Client()
    client.cookies["sessionid"] = staff
    client.cookies["csrftoken"] = "a" * 32
    with rust_server(overrides=html_assets) as port:
        for path in paths:
            compare_admin_page(port, path, client)
            assert request(port, path, cookie=board)[1]["Location"] == "/403/"
        for path in (
            f"{assignments}add/", f"{events}add/",
            f"{assignments}{row.id}/delete/", f"{events}{event.id}/delete/",
        ):
            assert request(port, path, cookie=staff)[0] == 403, path
            assert request(port, path, cookie=board)[1]["Location"] == "/403/", path
        for path in (f"{assignments}{row.id}/change/", f"{events}{event.id}/change/"):
            status, _, _ = request(
                port, path, method="POST", form={"note": "changed", "csrfmiddlewaretoken": "a" * 32},
                extra_headers={"Cookie": f"sessionid={staff}; csrftoken=" + "a" * 32},
            )
            assert status == 403
    event.refresh_from_db()
    assert event.note == "Added by hand & checked.\nSecond line."


@pytest.mark.django_db(transaction=True)
def test_admin_editable_model_forms_match_django_and_save(html_assets, subtests):
    from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
    from mittab.apps.tab.models import PublicRosterLogState, PublishedOutroundStage, UserTournamentSetupPreference

    staff = legacy_tab_session("admin-model-forms")
    user = get_user_model().objects.get(username="admin-model-forms")
    board = legacy_tab_session("admin-model-forms-board")
    board_user = get_user_model().objects.get(username="admin-model-forms-board")
    board_user.groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="Admin & School")
    gov = Team.objects.create(name="Wild & West", school=school, seed=0)
    opp = Team.objects.create(name="Common Side", school=school, seed=0)
    Round.objects.create(round_number=1, gov_team=gov, opp_team=opp, victor=Round.GOV)
    judge = Judge.objects.create(name="Debate Judge", rank=5)
    room = Room.objects.create(name="Auditorium", rank=3)
    tag = RoomTag.objects.create(tag="Main <Hall>", priority=1.25)
    room.tags.add(tag)
    gov.required_room_tags.add(tag)
    judge.required_room_tags.add(tag)
    bye = Bye.objects.create(bye_team=gov, round_number=2)
    noshow = NoShow.objects.create(no_show_team=opp, round_number=2)
    judge_checkin = CheckIn.objects.create(judge=judge, round_number=1)
    room_checkin = RoomCheckIn.objects.create(room=room, round_number=1)
    breaking = BreakingTeam.objects.create(team=gov, seed=1, effective_seed=2, type_of_team=0)
    preferences = UserTournamentSetupPreference.objects.create(user=user, hide_tournament_todo=True)
    setting = TabSettings.set("test-admin-setting", "Fixture & value")
    client = Client()
    csrf = "a" * 32
    client.cookies["sessionid"] = staff
    client.cookies["csrftoken"] = csrf
    csrf_headers = {"Cookie": f"sessionid={staff}; csrftoken={csrf}"}

    def submit(port, path, submitted, headers=csrf_headers, token=csrf):
        fields = [(key, item) for key, values in submitted.items()
                  for item in (values if isinstance(values, list) else [values])]
        return request(port, path, method="POST", form=[*fields, ("csrfmiddlewaretoken", token)], extra_headers=headers)

    records = [
        ("bye", bye), ("noshow", noshow), ("checkin", judge_checkin), ("roomcheckin", room_checkin),
        ("breakingteam", breaking), ("usertournamentsetuppreference", preferences), ("roomtag", tag), ("tabsettings", setting),
    ]
    base = "/admin/tab/"
    with rust_server(overrides=html_assets) as port:
        for name, obj in records:
            model = f"{base}{name}/"
            for path in (f"{model}add/", f"{model}{obj.id}/change/", f"{model}{obj.id}/history/", f"{model}{obj.id}/delete/"):
                with subtests.test(path=path):
                    compare_admin_page(port, path, client)
                    assert request(port, path, cookie=board)[1]["Location"] == "/403/"
            with subtests.test(bulk=model):
                compare_admin_page(port, model, client, post={
                    "action": "delete_selected", "_selected_action": obj.id, "index": 0,
                })
        forms = [
            (f"{base}roomtag/add/?_to_field=id&_popup=1", None),
            (f"{base}checkin/add/", {"judge": judge.id, "round_number": 1}),
            (f"{base}roomcheckin/add/", {"room": 99_999_999, "round_number": "bad"}),
            (f"{base}roomtag/add/", {"tag": "", "priority": "100.1"}),
            (f"{base}roomtag/add/", {"tag": "Malformed sign", "priority": "-+1"}),
            (f"{base}breakingteam/add/", {"team": gov.id, "seed": 1, "effective_seed": 4, "type_of_team": 1}),
            (f"{base}tabsettings/{setting.id}/change/?_changelist_filters=p%3D1", None),
        ]
        for path, post in forms:
            with subtests.test(path=path, post=post):
                compare_admin_page(port, path, client, post=post)

        assert submit(port, f"{base}roomtag/add/", {"tag": "Forbidden", "priority": "3.00"},
                      headers={"Cookie": f"sessionid={board}; csrftoken={csrf}"})[1]["Location"] == "/403/"
        assert submit(port, f"{base}roomtag/add/", {"tag": "Forbidden", "priority": "3.00"}, token="bad")[0] == 403
        assert not RoomTag.objects.filter(tag__in=["Forbidden", "Malformed sign"]).exists()

        saved = submit(port, f"{base}roomtag/add/", {"tag": "Reserved <Room>", "priority": "25e-2", "_continue": 1})
        new_tag = RoomTag.objects.get(tag="Reserved <Room>")
        assert new_tag.priority == Decimal("0.25")
        change = f"{base}roomtag/{new_tag.id}/change/"
        assert saved[0] == 302 and saved[1]["Location"] == change
        notice = load_cookies(saved[1])["mittab_admin_notice"]
        assert notice["httponly"]
        cookie = csrf_headers["Cookie"] + f"; {notice.key}={notice.value}"
        status, headers, html = request(port, change, extra_headers={"Cookie": cookie})
        assert status == 200 and b"was added successfully. You may edit it again below." in html
        assert b"Reserved &lt;Room&gt;" in html and load_cookies(headers)[notice.key]["max-age"] == "0"
        assert b"was added successfully" not in request(port, change, extra_headers=csrf_headers)[2]
        fresh_client = Client()
        fresh_client.force_login(user)
        fresh_session = fresh_client.cookies["sessionid"].value
        foreign_cookie = f"sessionid={fresh_session}; csrftoken={csrf}; {notice.key}={notice.value}"
        assert b"was added successfully" not in request(port, change, extra_headers={"Cookie": foreign_cookie})[2]
        tampered = csrf_headers["Cookie"] + f"; {notice.key}={notice.value.replace('.continue.', '.another.')}"
        assert b"was added successfully" not in request(port, change, extra_headers={"Cookie": tampered})[2]

        assert submit(port, f"{base}bye/{bye.id}/change/", {
            "bye_team": opp.id, "round_number": 3, "_addanother": 1,
        })[1]["Location"] == f"{base}bye/add/"
        bye.refresh_from_db()
        assert (bye.bye_team_id, bye.round_number) == (opp.id, 3)

        assert submit(port, f"{base}noshow/{noshow.id}/change/", {
            "no_show_team": gov.id, "round_number": 3,
        })[0] == 302
        noshow.refresh_from_db()
        assert (noshow.no_show_team_id, noshow.round_number) == (gov.id, 3)

        assert submit(port, f"{base}checkin/{judge_checkin.id}/change/", {
            "judge": judge.id, "round_number": 2,
        })[0] == 302
        judge_checkin.refresh_from_db()
        assert judge_checkin.round_number == 2

        assert submit(port, f"{base}roomcheckin/add/", {"room": room.id, "round_number": 2})[0] == 302
        assert RoomCheckIn.objects.filter(room=room, round_number=2).exists()
        assert submit(port, f"{base}breakingteam/{breaking.id}/change/", {
            "team": gov.id, "seed": 2, "effective_seed": 3, "type_of_team": 1,
        })[0] == 302
        breaking.refresh_from_db()
        assert (breaking.seed, breaking.effective_seed, breaking.type_of_team) == (2, 3, 1)

        assert submit(port, f"{base}usertournamentsetuppreference/{preferences.id}/change/", {"user": user.id})[0] == 302
        preferences.refresh_from_db()
        assert not preferences.hide_tournament_todo

        assert submit(port, f"{base}tabsettings/{setting.id}/change/", {
            "key": setting.key, "value": -2, "value_string": "",
        })[0] == 302
        setting.refresh_from_db()
        assert setting.value == -2 and setting.value_string is None

        settings_url = f"{base}tabsettings/"
        assert not PublicRosterLogState.objects.exists()
        assert submit(port, f"{settings_url}add/", {"key": "teams_public", "value": 1})[0] == 302
        first_publication = PublicRosterLogState.objects.get()
        assert first_publication.started_at and first_publication.first_published_at
        published_setting = TabSettings.objects.get(key="teams_public")
        assert submit(port, f"{settings_url}{published_setting.id}/change/", {"key": "teams_public", "value": 0})[0] == 302
        assert PublicRosterLogState.objects.get().first_published_at == first_publication.first_published_at

        Outround.objects.create(num_teams=2, type_of_round=Outround.VARSITY, gov_team=gov, opp_team=opp, victor=Outround.GOV)
        assert not PublishedOutroundStage.objects.exists()
        assert submit(port, f"{settings_url}add/", {"key": "var_teams_visible", "value": 2})[0] == 302
        stage = PublishedOutroundStage.objects.get(type_of_round=Outround.VARSITY, num_teams=2)
        assert dict(PublishedTeamPlacement.objects.filter(stage=stage).values_list("team_id", "place")) == {gov.id: 1, opp.id: 2}
        visibility = TabSettings.objects.get(key="var_teams_visible")
        assert submit(port, f"{settings_url}{visibility.id}/change/", {"key": "archived-visibility", "value": 2})[0] == 302
        assert not TabSettings.objects.filter(key="var_teams_visible").exists()
        assert PublishedTeamPlacement.objects.filter(stage=stage).count() == 2

        popup_status, _, popup = submit(port, f"{base}roomtag/add/", {
            "tag": "Partner’s room", "priority": "4.00", "_popup": 1, "_to_field": "id",
        })
        assert popup_status == 200 and b"django-admin-popup-response-constants" in popup
        assert b"Partner\\u2019s room" in popup

        # Confirming a filtered bulk action affects only the records in that filter.
        status, _, preview = submit(port, f"{base}roomtag/?id__exact={tag.id}", {
            "action": "delete_selected", "select_across": 1, "index": 0,
        })
        assert status == 200 and b"delete-selected-confirmation" in preview
        assert b"Reserved &lt;Room&gt;" not in preview
        filtered_list = f"{base}roomtag/?id__exact={tag.id}"
        deleted = submit(port, filtered_list, {
            "action": "delete_selected", "post": "yes", "_selected_action": [tag.id, new_tag.id],
        })
        assert deleted[0] == 302 and deleted[1]["Location"] == filtered_list
        deletion_notice = load_cookies(deleted[1])["mittab_admin_notice"]
        with_notice = csrf_headers["Cookie"] + f"; {deletion_notice.key}={deletion_notice.value}"
        assert b"Successfully deleted 1 room tag." in request(port, filtered_list, extra_headers={"Cookie": with_notice})[2]
        forged = with_notice.replace(".bulk-1.", ".bulk-999.")
        assert b"Successfully deleted" not in request(port, filtered_list, extra_headers={"Cookie": forged})[2]
        assert not RoomTag.objects.filter(id=tag.id).exists()
        assert RoomTag.objects.filter(id=new_tag.id).exists()
        assert not room.tags.exists() and not gov.required_room_tags.exists() and not judge.required_room_tags.exists()
        assert submit(port, f"{base}bye/{bye.id}/delete/", {"post": "yes"})[0] == 302
        assert not Bye.objects.filter(id=bye.id).exists()
        assert set(LogEntry.objects.filter(user=user).values_list("action_flag", flat=True)) == {ADDITION, CHANGE, DELETION}
        compare_admin_page(port, f"{base}roomtag/{new_tag.id}/history/", client)


@pytest.mark.django_db(transaction=True)
def test_admin_roster_forms_match_django_and_preserve_related_records(html_assets, subtests):
    from django.contrib.admin.models import CHANGE, LogEntry

    from mittab.apps.tab.models import RankingGroup

    session = legacy_tab_session("admin-roster")
    user = get_user_model().objects.get(username="admin-roster")
    board = legacy_tab_session("admin-roster-board")
    board_user = get_user_model().objects.get(username="admin-roster-board")
    board_user.groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="Roster & School")
    other_school = School.objects.create(name="Other School")
    gov = Team.objects.create(name="West <Team>", school=other_school, seed=0)
    opp = Team.objects.create(name="East Team", school=other_school, seed=0)
    judge = Judge.objects.create(name="Debate & Judge", rank=5)
    room = Room.objects.create(name="Auditorium", rank=3)
    assigned_room = Room.objects.create(name="East lecture hall", rank=5)
    debate = Round.objects.create(round_number=1, gov_team=gov, opp_team=opp, room=assigned_room, victor=Round.GOV)
    tag = RoomTag.objects.create(tag="Main <Hall>", priority=1.25)
    second_tag = RoomTag.objects.create(tag="Aisle seats", priority=2)
    discarded_tag = RoomTag.objects.create(tag="Discarded", priority=3)
    room.tags.add(discarded_tag)
    room.tags.add(tag)
    checkin = RoomCheckIn.objects.create(room=room, round_number=1)
    debater = Debater.objects.create(name="Ada & Debater", school=school, novice_status=0, tiebreaker=128)
    other_debater = Debater.objects.create(name="Grace", novice_status=1, tiebreaker=256)
    gov.debaters.add(other_debater)
    score = RoundStats.objects.create(debater=debater, round=debate, speaks=27, ranks=2)
    group = RankingGroup.objects.create(name="Fixture & Club")
    group.teams.add(gov)
    group.debaters.add(debater)
    scratch = Scratch.objects.create(judge=judge, team=gov, scratch_type=0, created_by=user)
    client = Client()
    csrf = "a" * 32
    client.cookies["sessionid"] = session
    client.cookies["csrftoken"] = csrf

    def submit(port, path, fields, session_id=session, token=csrf):
        return request(port, path, method="POST", form={**fields, "csrfmiddlewaretoken": token},
                       extra_headers={"Cookie": f"sessionid={session_id}; csrftoken={csrf}"})

    base = "/admin/tab/"
    with rust_server(overrides=html_assets) as port:
        records = [("school", school), ("debater", debater), ("room", room),
                   ("rankinggroup", group), ("scratch", scratch)]
        for name, record in records:
            model = f"{base}{name}/"
            for path in (f"{model}add/", f"{model}{record.id}/change/",
                         f"{model}{record.id}/history/", f"{model}{record.id}/delete/"):
                with subtests.test(path=path):
                    compare_admin_page(port, path, client, deletion_scope=name == "room" and path.endswith("/delete/"))
                    assert request(port, path, cookie=board)[1]["Location"] == "/403/"
            with subtests.test(bulk=model):
                compare_admin_page(port, model, client, post={
                    "action": "delete_selected", "_selected_action": record.id, "index": 0,
                }, deletion_scope=name == "room")
        invalid = [
            ("school", {"name": school.name, "apda_id": "invalid"}),
            ("debater", {"name": debater.name, "email": "bad", "tiebreaker": 256,
                         "novice_status": "invalid", "school": 99999}),
            ("debater", {"name": "Unquoted colon", "email": "alex:smith@example.org", "novice_status": 0}),
            ("room", {"name": room.name, "rank": "24.123", "tags": [second_tag.id, 99999]}),
            ("rankinggroup", {"name": "Group", "debaters": "non-number"}),
            ("scratch", {"judge": judge.id, "team": gov.id, "scratch_type": 0}),
            ("scratch", {}),
        ]
        for name, fields in invalid:
            with subtests.test(invalid=name, fields=fields):
                compare_admin_page(port, f"{base}{name}/add/", client, post=fields)

        room_path = f"{base}room/{room.id}/change/"
        tags = Room.tags.through.objects
        original_link = tags.get(room_id=room.id, roomtag_id=tag.id).id
        assert submit(port, room_path, {
            "name": "West lecture hall", "rank": "4.25", "tags": [second_tag.id, tag.id, tag.id],
        })[0] == 302
        room.refresh_from_db()
        assert (room.name, room.rank) == ("West lecture hall", Decimal("4.25"))
        assert set(room.tags.values_list("id", flat=True)) == {tag.id, second_tag.id}
        assert tags.get(room_id=room.id, roomtag_id=tag.id).id == original_link
        compare_admin_page(port, room_path, client)

        group_path = f"{base}rankinggroup/{group.id}/change/"
        assert submit(port, group_path, {"name": group.name, "teams": [gov.id, opp.id],
                                        "debaters": [debater.id, other_debater.id]})[0] == 302
        assert set(group.teams.values_list("id", flat=True)) == {gov.id, opp.id}
        assert set(group.debaters.values_list("id", flat=True)) == {debater.id, other_debater.id}
        compare_admin_page(port, f"{base}rankinggroup/{group.id}/history/", client)
        assert LogEntry.objects.get(content_type__model="rankinggroup", action_flag=CHANGE).get_change_message() == (
            "Changed Teams and Debaters."
        )

        debater_path = f"{base}debater/{debater.id}/change/"
        assert submit(port, debater_path, {"name": debater.name, "school": school.id, "novice_status": 1,
                                          "email": "ada@example.org", "qualified": "on", "tiebreaker": 128})[0] == 302
        debater.refresh_from_db()
        assert (debater.novice_status, debater.email, debater.qualified) == (1, "ada@example.org", True)
        assert 1 <= debater.tiebreaker <= 65535 and debater.tiebreaker not in (128, 256)
        assert submit(port, f"{base}debater/add/", {
            "name": "New roster debater", "novice_status": 0, "email": "alex@[192.0.2.1]",
        })[0] == 302
        new_debater = Debater.objects.get(name="New roster debater")
        assert new_debater.school is None and new_debater.email == "alex@[192.0.2.1]"

        fields = {"judge": judge.id, "team": opp.id, "scratch_type": 1, "created_by": board_user.id}
        assert submit(port, f"{base}scratch/add/", fields, session_id=board)[1]["Location"] == "/403/"
        assert submit(port, f"{base}scratch/add/", fields, token="bad")[0] == 403
        assert not Scratch.objects.filter(team=opp).exists()
        assert submit(port, f"{base}scratch/add/", fields)[0] == 302
        added = Scratch.objects.get(team=opp)
        assert added.created_by_id == user.id and added.created_at is not None
        added_time = added.created_at
        assert submit(port, f"{base}scratch/{added.id}/change/", {**fields, "scratch_type": 0,
                                                                "created_at": "2000-01-01"})[0] == 302
        added.refresh_from_db()
        assert (added.scratch_type, added.created_by_id, added.created_at) == (0, user.id, added_time)
        events = list(added.audit_events.order_by("id").values_list("event_type", "user_id", "changes"))
        assert events == [("create", user.id, {}), ("edit", user.id, {"fields": ["scratch_type"]})]
        compare_admin_page(port, f"{base}scratch/{added.id}/change/", client)
        compare_admin_page(port, f"{base}scratch/{added.id}/history/", client)

        TabSettings.set("teams_public", 1)
        Team.objects.filter(id=gov.id).update(hybrid_school=school.id)
        assert submit(port, f"{base}debater/{other_debater.id}/change/", {
            "name": "Grace & Roster", "novice_status": 1, "school": school.id,
        })[0] == 302
        assert submit(port, f"{base}school/{school.id}/change/", {"name": "Renamed School"})[0] == 302

        for protected in (f"{base}school/{other_school.id}/delete/",
                          f"{base}debater/{other_debater.id}/delete/",
                          f"{base}room/{assigned_room.id}/delete/"):
            status, _, preview = request(port, protected, cookie=session)
            assert status == 200 and b"Cannot delete" in preview and b'value="Yes, I' not in preview
            assert submit(port, protected, {"post": "yes"})[0] == 409
        assert submit(port, f"{base}school/", {"action": "delete_selected", "post": "yes",
                                             "_selected_action": [school.id, other_school.id]})[0] == 409
        assert School.objects.filter(id=school.id).exists()
        assert submit(port, f"{base}school/{school.id}/delete/", {"post": "yes"})[0] == 302
        assert not School.objects.filter(id=school.id).exists()
        debater.refresh_from_db()
        assert debater.school_id is None
        assert list(PublicRosterChange.objects.order_by("id").values_list(
            "list_kind", "message", "involves_debaters"
        )) == [
            ("teams", "Changed debater name for “Grace & Roster”: Grace → Grace & Roster.", True),
            ("teams", "Changed debater affiliation for “Grace & Roster”: None → Roster & School.", True),
            ("teams", "Changed affiliation name: Roster & School → Renamed School.", False),
            ("teams", "Changed debater affiliation name: Roster & School → Renamed School.", True),
            ("teams", "Removed hybrid affiliation “Renamed School” from team “West <Team>”.", False),
            ("teams", "Removed affiliation “Renamed School” from debater “Grace & Roster”.", True),
        ]
        assert submit(port, f"{base}debater/{debater.id}/delete/", {"post": "yes"})[0] == 302
        assert not RoundStats.objects.filter(id=score.id).exists()
        audit = AuditEvent.objects.get(content_type__model="round", object_id=debate.id, user=user)
        assert audit.changes["public"]["source"] == "admin scores"
        assert audit.changes["public"]["before"]["scores"][0]["debater"] == debater.id
        assert audit.changes["public"]["after"]["scores"] == []
        assert not group.debaters.filter(id=debater.id).exists()
        room_deletion = f"{base}room/{room.id}/delete/"
        _, _, preview = request(port, room_deletion, cookie=session)
        assert submit(port, room_deletion, {"post": "yes", "mittab_delete_scope": admin_delete_proof(preview)})[0] == 302
        assert not RoomCheckIn.objects.filter(id=checkin.id).exists()
        assert not tags.filter(room_id=room.id).exists()
        assert submit(port, f"{base}rankinggroup/{group.id}/delete/", {"post": "yes"})[0] == 302
        assert Team.objects.filter(id=gov.id).exists() and Debater.objects.filter(id=other_debater.id).exists()
        assert submit(port, f"{base}scratch/{added.id}/delete/", {"post": "yes"})[0] == 302
        assert not Scratch.objects.filter(id=added.id).exists()
        assert added.audit_events.count() == 2


@pytest.mark.django_db(transaction=True)
def test_admin_team_judge_forms_and_deletion_match_django(html_assets, subtests):
    from django.contrib.admin.models import CHANGE, LogEntry
    from mittab.apps.registration.models import Registration
    from mittab.apps.tab.models import (
        ManualJudgeAssignment, PublishedOutroundStage, RankingGroup, TeamCodeEmailLog, WrittenRFDEmailLog,
    )

    session = legacy_tab_session("team-judge-admin")
    user = get_user_model().objects.get(username="team-judge-admin")
    board = legacy_tab_session("team-judge-admin-board")
    board_user = get_user_model().objects.get(username="team-judge-admin-board")
    board_user.groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="North & School")
    hybrid = School.objects.create(name="South <School>")
    registration = Registration.objects.create(school=school, email="register@example.org")
    members = [
        Debater.objects.create(name=name, novice_status=0, school=sch)
        for name, sch in (("Alana & Ada", school), ("Bea <Team>", hybrid), ("Carmen", school))
    ]
    team = Team.objects.create(name="West & Team", school=school, hybrid_school=hybrid, seed=1,
                               team_code="fixture-team-code", created_by=user, registration=registration, tiebreaker=101)
    opponent = Team.objects.create(name="East <Team>", school=school, seed=0)
    team.debaters.add(*members[:2])
    tag = RoomTag.objects.create(tag="Accessible <Space>", priority=1)
    team.required_room_tags.add(tag)
    judge = Judge.objects.create(name="Original Judge", rank=4, reported_experience=5, ballot_code="ballot-fixture",
                                 created_by=user, request_wing=True, registration=registration)
    wing = Judge.objects.create(name="Wings Judge", rank=3)
    judge.schools.add(school)
    judge.required_room_tags.add(tag)
    debate = Round.objects.create(round_number=1, gov_team=team, opp_team=opponent, chair=judge, victor=Round.GOV)
    debate.judges.add(judge, wing)
    RoundStats.objects.create(round=debate, debater=members[0], speaks=27, ranks=1)
    WrittenRFDEmailLog.objects.create(round=debate, email="rfd@example.org")
    final = Outround.objects.create(num_teams=2, type_of_round=0, gov_team=team, opp_team=opponent, chair=judge,
                                    victor=Outround.GOV)
    final.judges.add(judge, wing)
    Scratch.objects.create(team=team, judge=judge, scratch_type=1)
    Bye.objects.create(bye_team=team, round_number=2)
    NoShow.objects.create(no_show_team=team, round_number=2)
    CheckIn.objects.create(judge=judge, round_number=1)
    JudgeExpectedCheckIn.objects.create(judge=judge, round_number=1)
    BreakingTeam.objects.create(team=team, seed=1, effective_seed=1, type_of_team=0)
    TeamCodeEmailLog.objects.create(team=team, email="code@example.org")
    group = RankingGroup.objects.create(name="Ranking cohort")
    group.teams.add(team)
    ManualJudgeAssignment.objects.filter(round=debate).delete()
    assignment = ManualJudgeAssignment.objects.create(round=debate, judge=wing, created_by=user)
    client = Client()
    csrf = "a" * 32
    client.cookies["sessionid"] = session
    client.cookies["csrftoken"] = csrf
    team_root, judge_root = "/admin/tab/team/", "/admin/tab/judge/"
    team_url = f"{team_root}{team.id}/"
    judge_url = f"{judge_root}{judge.id}/"

    def submit(port, path, fields, sid=session, token=csrf, preview=None):
        cookies = f"sessionid={sid}; csrftoken={csrf}"
        if preview is not None:
            fields = {**fields, "mittab_delete_scope": preview}
        return request(port, path, method="POST", form={**fields, "csrfmiddlewaretoken": token},
                       extra_headers={"Cookie": cookies})

    def scope(html):
        return re.search(rb'name="mittab_delete_scope" value="([a-f0-9]{64})"', html)[1].decode()

    def deletion_preview(port, path):
        status, _, html = request(port, path, cookie=session)
        assert status == 200
        return scope(html)

    with rust_server(overrides=html_assets) as port:
        for root, edit in [(team_root, team_url), (judge_root, judge_url)]:
            for path in (f"{root}add/", f"{edit}change/", f"{edit}history/", f"{edit}delete/"):
                with subtests.test(path=path):
                    compare_admin_page(port, path, client)
                    assert request(port, path, cookie=board)[1]["Location"] == "/403/"
        popup = "?_to_field=id&_popup=1"
        compare_admin_page(port, f"{judge_root}add/{popup}", client)
        compare_admin_page(port, f"{team_url}change/{popup}", client)

        for add, form in [
            (team_root, {}),
            (team_root, {"name": team.name, "debaters": [99999], "school": 99999, "seed": "invalid"}),
            (judge_root, {}),
            (judge_root, {"name": judge.name, "rank": "100.0", "reported_experience": -1, "ballot_code": "broken-code!!"}),
            (judge_root, {"name": "Duplicate ballot code", "rank": 5, "reported_experience": 11,
                          "schools": [school.id], "ballot_code": judge.ballot_code}),
        ]:
            with subtests.test(invalid=form):
                compare_admin_page(port, f"{add}add/", client, post=form)

        edit_team = {"name": "Updated West", "school": school.id, "debaters": [members[0].id, members[2].id],
                     "seed": 2, "break_preference": 1, "checked_in": "on",
                     "required_room_tags": [tag.id], "registration": registration.id, "ranking_public": "on",
                     "tiebreaker": 101, "team_code": "forged-code", "created_by": board_user.id}
        edit_judge = {"name": "Updated Judge", "rank": "6.50", "reported_experience": 10,
                      "schools": [hybrid.id], "ballot_code": "existing-updated",
                      "is_dino": "on", "no_wings": "on", "required_room_tags": [tag.id], "registration": registration.id,
                      "created_by": board_user.id, "created_at": "2000-01-01"}
        assert submit(port, f"{judge_url}change/", edit_judge, sid=board)[1]["Location"] == "/403/"
        assert submit(port, f"{judge_url}change/", edit_judge, token="bad")[0] == 403
        assert Judge.objects.get(id=judge.id).name == "Original Judge"

        before = Team.debaters.through.objects.get(team=team, debater=members[0]).id
        TabSettings.set("teams_public", 1)
        created_on = judge.created_at
        assert submit(port, f"{team_url}change/", edit_team)[0] == 302
        assert submit(port, f"{judge_url}change/", edit_judge)[0] == 302
        team.refresh_from_db()
        judge.refresh_from_db()
        assert (team.seed, team.hybrid_school_id, team.team_code, team.created_by_id) == (2, None, "fixture-team-code", user.id)
        assert 1 <= team.tiebreaker <= 65535 and team.tiebreaker != 101
        assert set(team.debaters.values_list("id", flat=True)) == {members[0].id, members[2].id}
        assert Team.debaters.through.objects.get(team=team, debater=members[0]).id == before
        assert (judge.is_dino, judge.rank, judge.ballot_code, judge.created_by_id, judge.created_at) == (
            True, Decimal("6.50"), "existing-updated", user.id, created_on)
        assert list(judge.schools.values_list("id", flat=True)) == [hybrid.id]
        assert judge.audit_events.get().changes["is_dino"] == {"before": False, "after": True}
        assert LogEntry.objects.get(content_type__model="judge", action_flag=CHANGE).get_change_message().startswith("Changed Name, Rank")
        assert list(PublicRosterChange.objects.order_by("id").values_list("list_kind", "message", "involves_debaters")) == [
            ("teams", "Changed affiliation for “Updated West”: South <School> → None.", False),
            ("teams", "Removed debater “Bea <Team>” from team “Updated West”.", True),
            ("teams", "Added debater “Carmen” on team “Updated West”.", True),
            ("judges", "Changed name for “Updated Judge”: Original Judge → Updated Judge.", False),
            ("judges", "Removed affiliation “North & School” from judge “Updated Judge”.", False),
            ("judges", "Added affiliation “South <School>” for judge “Updated Judge”.", False),
        ]
        for url in (team_url, judge_url):
            compare_admin_page(port, f"{url}change/", client)
            compare_admin_page(port, f"{url}history/", client)

        added = {"name": "Added team", "school": school.id, "debaters": members[1].id,
                 "seed": 0, "break_preference": 0}
        assert submit(port, f"{team_root}add/", added)[0] == 302
        new_team = Team.objects.get(name="Added team")
        assert new_team.team_code and new_team.created_at and new_team.created_by_id == user.id
        assert not new_team.checked_in and not new_team.ranking_public
        assert new_team.audit_events.get().changes == {}
        added = {"name": "Added judge", "rank": "2.75", "schools": school.id, "ballot_code": ""}
        assert submit(port, f"{judge_root}add/", added)[0] == 302
        new_judge = Judge.objects.get(name="Added judge")
        assert Judge.is_valid_ballot_code(new_judge)
        assert new_judge.ballot_code and new_judge.created_by_id == user.id
        assert new_judge.audit_events.get().changes == {}

        compare_admin_page(port, team_root, client, post={"action": "delete_selected", "index": 0,
                                                          "_selected_action": [team.id, new_team.id]})
        assert submit(port, f"{team_url}delete/", {"post": "yes"})[0] == 403
        assert submit(port, team_root, {"action": "delete_selected", "post": "yes",
                                       "_selected_action": [team.id, new_team.id]})[0] == 403
        assert Team.objects.filter(id__in=[team.id, new_team.id]).count() == 2
        assert ManualJudgeAssignment.objects.filter(id=assignment.id).exists()
        assignment.delete()

        for url in (team_url, judge_url):
            compare_admin_page(port, f"{url}delete/", client, deletion_scope=True)
        compare_admin_page(port, team_root, client, post={"action": "delete_selected", "index": 0,
                                                          "_selected_action": [team.id, opponent.id]},
                           deletion_scope=True)
        # A later stage cannot survive deletion of its prerequisite through a judge cascade.
        semifinal = Outround.objects.create(num_teams=4, type_of_round=0, gov_team=team,
                                           opp_team=opponent, chair=new_judge, victor=Outround.GOV)
        earlier_path = f"{judge_root}{new_judge.id}/delete/"
        status, _, preview = request(port, earlier_path, cookie=session)
        assert status == 200 and b"Resolve the later stage before deleting this pairing." in preview
        assert submit(port, earlier_path, {"post": "yes"})[0] == 409
        assert Outround.objects.filter(id__in=[semifinal.id, final.id]).count() == 2
        semifinal.delete()

        # A confirmed dependency graph must be the same graph that was shown to the operator.
        old_preview = deletion_preview(port, earlier_path)
        newly_assigned = Round.objects.create(round_number=3, gov_team=team, opp_team=opponent, chair=new_judge)
        assert deletion_preview(port, earlier_path) != old_preview
        status, _, response = submit(port, earlier_path, {"post": "yes"}, preview=old_preview)
        assert status == 409 and b"Review updated deletion" in response and b"Round 3 between" in response
        assert Round.objects.filter(id=newly_assigned.id, chair=new_judge).exists()
        newly_assigned.delete()

        judge_deletion = f"{judge_url}delete/"
        before_publication = deletion_preview(port, judge_deletion)
        published_final = PublishedOutroundStage.objects.create(type_of_round=0, num_teams=2)
        PublishedTeamPlacement.objects.create(team=team, place=1, stage=published_final)
        PublishedTeamPlacement.objects.create(team=opponent, place=2, stage=published_final)
        other_stage = PublishedOutroundStage.objects.create(type_of_round=1, num_teams=2)
        preserved = PublishedTeamPlacement.objects.create(team=opponent, place=1, stage=other_stage)
        assert submit(port, judge_deletion, {"post": "yes"}, preview=before_publication)[0] == 409
        before_result = deletion_preview(port, judge_deletion)
        RoundStats.objects.filter(round=debate).update(speaks=28)
        Outround.objects.filter(id=final.id).update(victor=Outround.OPP)
        assert submit(port, judge_deletion, {"post": "yes"}, preview=before_result)[0] == 409
        assert Judge.objects.filter(id=judge.id).exists() and Outround.objects.filter(id=final.id).exists()
        preview = deletion_preview(port, judge_deletion)
        assert submit(port, judge_deletion, {"post": "yes"}, preview=preview)[0] == 302
        assert not Judge.objects.filter(id=judge.id).exists()
        assert not Round.objects.filter(id=debate.id).exists()
        assert not Outround.objects.filter(id=final.id).exists()
        assert not PublishedOutroundStage.objects.filter(id=published_final.id).exists()
        assert PublishedTeamPlacement.objects.filter(id=preserved.id).exists()
        for model, object_id in (("round", debate.id), ("outround", final.id)):
            event = AuditEvent.objects.get(content_type__model=model, object_id=object_id, user=user)
            assert event.changes["public"]["source"] == "admin deletion"
            assert event.changes["public"]["before"]["chair"] == judge.id
            assert event.changes["public"]["after"] is None
        assert Judge.objects.filter(id=wing.id).exists()
        assert team.debaters.count() == 2

        bulk = {"action": "delete_selected", "_selected_action": [team.id, new_team.id]}
        status, _, html = submit(port, team_root, {**bulk, "index": 0})
        assert status == 200
        assert submit(port, team_root, {**bulk, "post": "yes"}, preview=scope(html))[0] == 302
        assert not Team.objects.filter(id=new_team.id).exists()
        assert not Team.objects.filter(id=team.id).exists()
        assert Team.objects.filter(id=opponent.id).exists()
        assert Debater.objects.filter(id__in=[member.id for member in members]).count() == 3
        assert Registration.objects.filter(id=registration.id).exists()
        assert not group.teams.filter(id=team.id).exists()


@pytest.mark.django_db(transaction=True)
def test_admin_round_and_outround_forms_match_django_and_record_corrections(html_assets, subtests):
    from mittab.apps.tab.models import ManualJudgeAssignment, PublishedOutroundStage, WrittenRFDEmailLog

    staff = legacy_tab_session("pairing-admin-parity")
    user = get_user_model().objects.get(username="pairing-admin-parity")
    board = legacy_tab_session("pairing-admin-parity-board")
    get_user_model().objects.get(username="pairing-admin-parity-board").groups.add(
        Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    TabSettings.set("var_teams_visible", 2)
    school = School.objects.create(name="Pairing Editor School")
    gov, opp = [Team.objects.create(name=name, school=school, seed=0)
                for name in ("Government <Team>", "Opposition & Team")]
    chair, wing, spare = [Judge.objects.create(name=name, rank=2)
                          for name in ("Catherine & Chair", "Winnie Wing", "Spare <Judge>")]
    room = Room.objects.create(name="Pairing & Room", rank=1)
    speaker = Debater.objects.create(name="Score fixture", novice_status=0)
    gov.debaters.add(speaker)
    debate = Round.objects.create(round_number=1, gov_team=gov, opp_team=opp, room=room, chair=chair,
                                  rfd="Original <statement> & reason\r\nSecond line")
    debate.judges.add(chair, wing)
    ManualJudgeAssignment.objects.filter(round=debate, judge=wing).update(created_by=user)
    kept_assignment = ManualJudgeAssignment.objects.get(round=debate, judge=wing)
    kept_link = Round.judges.through.objects.get(round=debate, judge=wing).id
    score = RoundStats.objects.create(round=debate, debater=speaker, debater_role="pm", speaks=27, ranks=1)
    email = WrittenRFDEmailLog.objects.create(round=debate, email="written@example.org")
    active_no_show = NoShow.objects.create(no_show_team=gov, round_number=1)
    other_no_show = NoShow.objects.create(no_show_team=gov, round_number=2)
    final = Outround.objects.create(num_teams=2, type_of_round=0, gov_team=gov, opp_team=opp, chair=chair,
                                    room=room, victor=Outround.GOV)
    final.judges.add(chair, wing)
    final_stage = PublishedOutroundStage.objects.create(type_of_round=0, num_teams=2)
    PublishedTeamPlacement.objects.create(stage=final_stage, team=gov, place=1)
    PublishedTeamPlacement.objects.create(stage=final_stage, team=opp, place=2)

    client = Client()
    csrf = "a" * 32
    client.cookies["sessionid"] = staff
    client.cookies["csrftoken"] = csrf
    round_list, outround_list = "/admin/tab/round/", "/admin/tab/outround/"
    debate_url, final_url = f"{round_list}{debate.id}/", f"{outround_list}{final.id}/"

    def post(port, path, fields, session=staff, token=csrf):
        return request(port, path, method="POST", form={**fields, "csrfmiddlewaretoken": token},
                       extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"})

    def save(port, path, fields):
        status, _, html = request(port, path, cookie=staff)
        assert status == 200
        return post(port, path, {**fields, "mittab_pairing_revision": admin_edit_proof(html)})

    def pair_audit(pairing):
        return AuditEvent.objects.filter(
            content_type__model=pairing._meta.model_name,
            object_id=pairing.id,
            changes__public__source="admin correction",
        )

    prelim_fields = {"round_number": 1, "gov_team": gov.id, "opp_team": opp.id, "chair": spare.id,
                     "judges": [wing.id, spare.id], "pullup": Round.GOV, "room": room.id,
                     "victor": Round.OPP, "rfd": "Private <reason> & example\nKeep the second line."}
    final_fields = {"num_teams": 2, "type_of_round": Outround.VARSITY, "gov_team": gov.id, "opp_team": opp.id,
                    "chair": wing.id, "judges": [wing.id, spare.id], "room": room.id,
                    "victor": Outround.OPP, "sidelock": "on", "choice": Outround.GOV}

    with rust_server(overrides=html_assets) as port:
        for root, url, proof in [(round_list, debate_url, False), (outround_list, final_url, True)]:
            for endpoint in (f"{root}add/", f"{url}change/", f"{url}history/", f"{url}delete/"):
                with subtests.test(endpoint=endpoint):
                    compare_admin_page(port, endpoint, client, deletion_scope=proof and endpoint.endswith("/delete/"),
                                       pairing_revision=endpoint.endswith("/change/"))
                    assert request(port, endpoint, cookie=board)[1]["Location"] == "/403/"
        compare_admin_page(port, f"{round_list}add/?chair={spare.id}&judges={chair.id},{spare.id}&gov_team={gov.id}", client)
        compare_admin_page(port, f"{round_list}add/", client, post={})
        compare_admin_page(port, f"{outround_list}add/", client, post={**final_fields, "num_teams": "no"})
        compare_admin_page(port, f"{debate_url}change/", client,
                           post={**prelim_fields, "judges": [wing.id]}, pairing_revision=True)
        compare_admin_page(port, f"{final_url}change/", client, post={**final_fields, "chair": spare.id, "judges": [wing.id]}, pairing_revision=True)
        compare_admin_page(port, round_list, client, post={"action": "delete_selected",
                                                          "_selected_action": debate.id, "index": 0})

        assert post(port, f"{debate_url}change/", prelim_fields, session=board)[1]["Location"] == "/403/"
        assert post(port, f"{final_url}change/", final_fields, token="invalid")[0] == 403
        assert post(port, f"{debate_url}delete/", {"post": "yes"})[0] == 403
        assert post(port, round_list, {"action": "delete_selected", "_selected_action": debate.id, "post": "yes"})[0] == 403
        assert RoundStats.objects.filter(id=score.id).exists()

        _, _, html = request(port, f"{debate_url}change/", cookie=staff)
        previous_spoken = "Fixture ballot entered after the editor opened"
        Round.objects.filter(id=debate.id).update(victor=Round.GOV, rfd=previous_spoken)
        status, _, html = post(port, f"{debate_url}change/", {
            **prelim_fields, "mittab_pairing_revision": admin_edit_proof(html),
        })
        assert status == 409 and previous_spoken.encode() in html
        assert Round.objects.get(id=debate.id).victor == Round.GOV
        Round.objects.filter(id=debate.id).update(victor=Round.UNKNOWN, rfd=debate.rfd)

        assert save(port, f"{debate_url}change/", {**prelim_fields, "_continue": 1})[1]["Location"] == f"{debate_url}change/"
        debate.refresh_from_db()
        assert debate.result_entered_at and debate.victor == Round.OPP and debate.rfd == prelim_fields["rfd"]
        assert not NoShow.objects.filter(id=active_no_show.id).exists()
        assert NoShow.objects.filter(id=other_no_show.id).exists()
        assert set(debate.judges.values_list("id", flat=True)) == {wing.id, spare.id}
        assert Round.judges.through.objects.get(round=debate, judge=wing).id == kept_link
        assert ManualJudgeAssignment.objects.get(round=debate, judge=wing).id == kept_assignment.id
        assert ManualJudgeAssignment.objects.get(round=debate, judge=wing).created_by_id == user.id
        assert ManualJudgeAssignment.objects.get(round=debate, judge=spare).created_by_id is None
        assert not ManualJudgeAssignment.objects.filter(round=debate, judge=chair).exists()
        initial_time = debate.result_entered_at
        assert save(port, f"{debate_url}change/", {**prelim_fields, "victor": Round.GOV})[0] == 302
        debate.refresh_from_db()
        assert debate.result_entered_at == initial_time
        event = pair_audit(debate).order_by("id").first()
        assert event.changes["public"]["before"]["chair"] == chair.id
        assert event.changes["public"]["after"]["chair"] == spare.id
        assert "Private <reason>" not in json.dumps(event.changes)
        assert save(port, f"{debate_url}change/", {**prelim_fields, "victor": Round.UNKNOWN})[0] == 302
        debate.refresh_from_db()
        assert debate.result_entered_at is None

        assert save(port, f"{final_url}change/", final_fields)[1]["Location"] == outround_list
        final.refresh_from_db()
        assert final.sidelock and final.choice == Outround.GOV and final.victor == Outround.OPP
        assert set(final.judges.values_list("id", flat=True)) == {wing.id, spare.id}
        assert dict(PublishedTeamPlacement.objects.filter(stage=final_stage).values_list("team_id", "place")) == {
            gov.id: 2, opp.id: 1,
        }
        event = pair_audit(final).get()
        assert event.changes["public"]["after"]["victor"] == Outround.OPP
        compare_admin_page(port, f"{debate_url}change/", client, pairing_revision=True)
        compare_admin_page(port, f"{debate_url}history/", client)
        compare_admin_page(port, f"{final_url}history/", client)

        assert post(port, f"{round_list}add/", {**prelim_fields, "round_number": 3, "rfd": ""})[0] == 302
        added = Round.objects.get(round_number=3)
        assert added.rfd == "" and added.result_entered_at
        assert pair_audit(added).get().event_type == "create"
        assert post(port, f"{outround_list}add/", {**final_fields, "type_of_round": Outround.NOVICE})[0] == 302
        novice = Outround.objects.get(type_of_round=Outround.NOVICE)
        assert pair_audit(novice).get().event_type == "create"

        eliminated = Team.objects.create(name="Eliminated semifinal team", school=school, seed=0)
        semifinal = Outround.objects.create(num_teams=4, type_of_round=Outround.VARSITY,
                                           gov_team=gov, opp_team=eliminated, chair=wing,
                                           victor=Outround.GOV)
        semifinal.judges.add(wing)
        semifinal_path = f"{outround_list}{semifinal.id}/change/"
        semifinal_fields = {**final_fields, "num_teams": 4, "opp_team": eliminated.id,
                            "victor": Outround.OPP}
        status, _, html = save(port, semifinal_path, semifinal_fields)
        assert status == 409 and b"Re-pair the later stage before changing its qualifying team." in html
        semifinal.refresh_from_db()
        assert semifinal.victor == Outround.GOV
        assert save(port, semifinal_path, {**semifinal_fields, "victor": Outround.GOV})[0] == 302
        status, _, html = save(port, f"{final_url}change/", {**final_fields, "gov_team": eliminated.id})
        assert status == 409 and b"Choose distinct teams that are still eligible for this stage." in html
        final.refresh_from_db()
        assert final.gov_team_id == gov.id
        bye = Team.objects.create(name="Bye entrant", school=school, seed=0)
        assert save(port, f"{final_url}change/", {**final_fields, "opp_team": bye.id})[0] == 302
        assert save(port, f"{final_url}change/", final_fields)[0] == 302

        ManualJudgeAssignment.objects.filter(round__in=[debate, added]).delete()
        compare_admin_page(port, f"{debate_url}delete/", client, deletion_scope=True)
        _, _, single = request(port, f"{debate_url}delete/", cookie=staff)
        scope = {"action": "delete_selected", "_selected_action": [debate.id, added.id]}
        compare_admin_page(port, round_list, client, post={**scope, "index": 0}, deletion_scope=True)
        _, _, body = post(port, round_list, {**scope, "index": 0})
        token = admin_delete_proof(body)
        Round.objects.filter(id=debate.id).update(rfd="Fresh private RFD after deletion previews")
        status, _, refreshed = post(port, f"{debate_url}delete/", {
            "post": "yes", "mittab_delete_scope": admin_delete_proof(single),
        })
        assert status == 409 and b"Fresh private RFD" not in refreshed
        assert post(port, round_list, {**scope, "post": "yes", "mittab_delete_scope": token})[0] == 409
        assert Round.objects.filter(id__in=[debate.id, added.id]).count() == 2
        _, _, body = post(port, round_list, {**scope, "index": 0})
        assert post(port, round_list, {**scope, "post": "yes", "mittab_delete_scope": admin_delete_proof(body)})[0] == 302
        assert not RoundStats.objects.filter(id=score.id).exists()
        assert not WrittenRFDEmailLog.objects.filter(id=email.id).exists()
        assert AuditEvent.objects.get(content_type__model="round", object_id=debate.id,
                                      changes__public__source="admin deletion").object_repr == f"Round {debate.id}"

        _, _, body = request(port, f"{final_url}delete/", cookie=staff)
        assert post(port, f"{final_url}delete/", {"post": "yes", "mittab_delete_scope": admin_delete_proof(body)})[0] == 302
        assert not PublishedOutroundStage.objects.filter(id=final_stage.id).exists()
        assert AuditEvent.objects.get(content_type__model="outround", object_id=final.id,
                                      changes__public__source="admin deletion").object_repr == f"Outround {final.id}"
        assert Outround.objects.filter(id=novice.id).exists()

        # A room which has only been used for eliminations follows Django's room cascade.
        elimination_room = Room.objects.create(name="Elimination-only room", rank=7)
        Outround.objects.filter(id=novice.id).update(room=elimination_room)
        novice_stage = PublishedOutroundStage.objects.create(type_of_round=Outround.NOVICE, num_teams=2)
        PublishedTeamPlacement.objects.create(stage=novice_stage, team=opp, place=1)
        room_deletion = f"/admin/tab/room/{elimination_room.id}/delete/"
        compare_admin_page(port, room_deletion, client, deletion_scope=True)
        _, _, html = request(port, room_deletion, cookie=staff)
        assert post(port, room_deletion, {"post": "yes", "mittab_delete_scope": admin_delete_proof(html)})[0] == 302
        assert not Room.objects.filter(id=elimination_room.id).exists()
        assert not Outround.objects.filter(id=novice.id).exists()
        assert not PublishedOutroundStage.objects.filter(id=novice_stage.id).exists()
        assert Judge.objects.filter(id=wing.id).exists()
        assert Team.objects.filter(id=opp.id).exists()

def admin_delete_proof(body):
    return re.search(rb'name="mittab_delete_scope" value="([a-f0-9]{64})"', body)[1].decode()


def admin_edit_proof(body):
    return re.search(rb'name="mittab_pairing_revision" value="([a-f0-9]{64})"', body)[1].decode()


@pytest.mark.django_db(transaction=True)
def test_auth_admin_screens_and_mutations_match_django(html_assets, subtests):
    from django.contrib.admin.models import CHANGE, LogEntry
    from django.contrib.auth.hashers import check_password, make_password
    from django.contrib.auth.models import Permission
    from mittab.apps.tab.models import UserTournamentSetupPreference

    session = legacy_tab_session("auth-parity-admin")
    user_model = get_user_model()
    admin = user_model.objects.get(username="auth-parity-admin")
    board = legacy_tab_session("auth-parity-board")
    board_user = user_model.objects.get(username="auth-parity-board")
    board_group = Group.objects.create(name="Apda Board")
    board_user.groups.add(board_group)
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="Auth parity")
    gov, opp = [Team.objects.create(name=name, school=school, seed=0) for name in ("Auth Gov", "Auth Opp")]
    Round.objects.create(round_number=1, gov_team=gov, opp_team=opp, victor=Round.GOV)

    chosen = Group.objects.create(name="Fixture & Group")
    permission = Permission.objects.get(codename="view_debater")
    chosen.permissions.add(permission)
    password_hash = make_password("fixture-secret-for-test", salt="fixtureAccountSaltOnly", hasher="pbkdf2_sha256")
    account = user_model.objects.create(username="fixture+person", first_name="A & Name", last_name="Display",
                                         email="fixture@example.org", password=password_hash, is_staff=True,
                                         last_login=timezone.now())
    account.groups.add(chosen)
    account.user_permissions.add(permission)
    preference = UserTournamentSetupPreference.objects.create(user=account, hide_tournament_todo=True)
    LogEntry.objects.log_action(user_id=account.id, content_type_id=permission.content_type_id,
        object_id=gov.id, object_repr="Fixture & object", action_flag=CHANGE,
        change_message=[{"changed": {"fields": ["Fixture"]}}])

    client = Client()
    csrf = "a" * 32
    client.cookies["sessionid"] = session
    client.cookies["csrftoken"] = csrf
    users, groups = "/admin/auth/user/", "/admin/auth/group/"
    edit_user, edit_group = f"{users}{account.id}/change/", f"{groups}{chosen.id}/change/"

    def submit(port, path, fields, sid=session, token=csrf):
        return request(port, path, method="POST", form={**fields, "csrfmiddlewaretoken": token},
                       extra_headers={"Cookie": f"sessionid={sid}; csrftoken={csrf}"})

    with rust_server(overrides=html_assets) as port:
        for path in (
            users, groups, users + "?is_staff__exact=1&q=Fixture",
            users + "?o=-3.1", users + f"?groups__id__exact={chosen.id}",
            groups + "?q=Fixture", groups + "?_to_field=id&_popup=1",
            users + "add/", groups + "add/", edit_user, edit_group,
            f"{users}{account.id}/password/", f"{users}{account.id}/history/", f"{groups}{chosen.id}/history/",
            f"{users}{account.id}/delete/", f"{groups}{chosen.id}/delete/",
        ):
            with subtests.test(path=path):
                compare_admin_page(port, path, client, deletion_scope=path.endswith("/delete/"))
                assert request(port, path, cookie=board)[1]["Location"] == "/403/"
        for path, fields in [
            (users + "add/", {}),
            (users + "add/", {"username": "invalid name", "password1": "fixture-one", "password2": "fixture-two"}),
            (f"{users}{account.id}/password/", {"password1": "fixture-one", "password2": "fixture-two"}),
            (groups + "add/", {"name": chosen.name}),
        ]:
            with subtests.test(path=path, fields=tuple(fields)):
                compare_admin_page(port, path, client, post=fields)
        for path, id in ((users, account.id), (groups, chosen.id)):
            compare_admin_page(port, path, client, post={
                "action": "delete_selected", "_selected_action": id, "index": 0,
            }, deletion_scope=True)
        assert password_hash.encode() not in request(port, edit_user, cookie=session)[2]
        fields = {"username": "auth-new-fixture", "password1": "fixture-created-password", "password2": "fixture-created-password",
                  "is_staff": "on", "is_superuser": "on", "groups": board_group.id}
        assert submit(port, users + "add/", fields, sid=board)[1]["Location"] == "/403/"
        assert submit(port, users + "add/", fields, token="invalid")[0] == 403
        assert not user_model.objects.filter(username=fields["username"]).exists()
        created = submit(port, users + "add/", fields)
        fixture = user_model.objects.get(username=fields["username"])
        fixture_url = f"{users}{fixture.id}/change/"
        assert created[0] == 302 and created[1]["Location"] == fixture_url
        assert fixture.is_active and not fixture.is_staff and not fixture.is_superuser and not fixture.groups.exists()
        assert check_password(fields["password1"], fixture.password)
        compare_admin_page(port, f"{users}{fixture.id}/history/", client)
        updated = {"username": fixture.username, "first_name": "New first", "email": "updated@example.org",
                   "is_active": "on", "is_staff": "on", "groups": chosen.id, "user_permissions": permission.id,
                   "last_login_0": "2024-01-01", "last_login_1": "13:15:30",
                   "date_joined_0": "2023-01-02", "date_joined_1": "14:16:30", "_continue": 1}
        assert submit(port, fixture_url, updated)[1]["Location"] == fixture_url
        fixture.refresh_from_db()
        assert fixture.is_staff and not fixture.is_superuser
        assert list(fixture.groups.all()) == [chosen]
        assert list(fixture.user_permissions.all()) == [permission]
        assert fixture.date_joined.date().isoformat() == "2023-01-02"
        assert fixture.last_login.date().isoformat() == "2024-01-01"
        compare_admin_page(port, fixture_url, client)
        compare_admin_page(port, f"{users}{fixture.id}/history/", client)
        assert submit(port, f"{users}{admin.id}/change/", {"username": admin.username, "is_active": "on"})[0] == 422

        old_state = request(port, f"{groups}{chosen.id}/delete/", cookie=session)[2]
        second_perm = Permission.objects.get(codename="view_room")
        assert submit(port, edit_group, {"name": "Renamed & Group", "permissions": [permission.id, second_perm.id]})[0] == 302
        assert submit(port, f"{groups}{chosen.id}/delete/", {
            "post": "yes", "mittab_delete_scope": admin_delete_proof(old_state),
        })[0] == 409
        assert Group.objects.filter(id=chosen.id, name="Renamed & Group").exists()
        assert submit(port, f"{groups}{board_group.id}/change/", {"name": "Different role"})[0] == 422
        assert submit(port, f"{groups}{board_group.id}/delete/", {"post": "yes"})[0] == 422

        group_filter = f"{groups}?id__exact={chosen.id}"
        selection = {"action": "delete_selected", "_selected_action": [chosen.id, board_group.id], "index": 0}
        status, _, response = submit(port, group_filter, selection)
        assert status == 200
        assert submit(port, group_filter, {**selection, "post": "yes",
                                           "mittab_delete_scope": admin_delete_proof(response)})[0] == 302
        assert not Group.objects.filter(id=chosen.id).exists()
        assert Group.objects.filter(id=board_group.id).exists()
        assert user_model.objects.filter(id=fixture.id).exists()

        deletion = f"{users}{account.id}/delete/"
        _, _, preview = request(port, deletion, cookie=session)
        assert submit(port, deletion, {"confirm": "on"})[0] == 409
        attribution = AuditEvent.objects.create(content_type=permission.content_type, object_id=gov.id,
                                                object_repr="Fixture audit authorship", event_type="edit", user=account)
        assert submit(port, deletion, {"post": "yes", "mittab_delete_scope": admin_delete_proof(preview)})[0] == 409
        attribution.refresh_from_db()
        assert attribution.user_id == account.id
        _, _, preview = request(port, deletion, cookie=session)
        assert submit(port, deletion, {
            "post": "yes", "mittab_delete_scope": admin_delete_proof(preview),
        })[0] == 302
        assert not user_model.objects.filter(id=account.id).exists()
        attribution.refresh_from_db()
        assert attribution.user_id is None
        assert not UserTournamentSetupPreference.objects.filter(id=preference.id).exists()
        assert submit(port, f"{users}{admin.id}/delete/", {"post": "yes"})[0] == 422

        changed = submit(port, f"{users}{admin.id}/password/", {"password1": "new-staff-password", "password2": "new-staff-password"})
        assert changed[0] == 302
        refreshed = load_cookies(changed[1])["sessionid"].value
        assert refreshed != session and request(port, "/admin/", cookie=refreshed)[0] == 200
        old_login = request(port, "/admin/", cookie=session)
        assert old_login[0] != 200

        # A request authenticated before a credential change must check it again after waiting.
        from time import monotonic, sleep

        replacement = make_password("newer-staff-password")
        raced = {"password1": "stale-racing-password", "password2": "stale-racing-password"}
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                user_model.objects.select_for_update().get(id=admin.id)
                pending = executor.submit(submit, port, f"{users}{admin.id}/password/", raced, refreshed)
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SHOW FULL PROCESSLIST")
                        waiting = any(row[3] == connection.settings_dict["NAME"] and row[7]
                                      and "SELECT id FROM auth_user WHERE is_superuser = TRUE ORDER BY id FOR UPDATE" in row[7]
                                      for row in cursor.fetchall())
                    if waiting:
                        break
                    sleep(0.025)
                assert waiting, "The account mutation did not reach the transaction lock"
                user_model.objects.filter(id=admin.id).update(password=replacement)
            assert pending.result(timeout=6)[1]["Location"] == "/403/"
        admin.refresh_from_db()
        assert admin.password == replacement


def compare_admin_page(port, path, client, post=None, *, deletion_scope=False, pairing_revision=False,
                       registration_revision=False, configuration_revision=False, list_revision=False,
                       content_revision=False, motion_revision=False, staff_motion_revision=False):
    session = client.cookies["sessionid"].value
    csrf = client.cookies["csrftoken"].value
    form = {**post, "csrfmiddlewaretoken": csrf} if post is not None else None
    if staff_motion_revision and form is not None:
        status, _, previous = request(port, path, cookie=session)
        assert status == 200
        form["motion_revision"] = staff_motion_proof(previous)
    if motion_revision and form is not None:
        status, _, previous = request(port, path, cookie=session)
        assert status == 200
        form["mittab_motion_revision"] = motion_edit_proof(previous)
    if list_revision and form is not None:
        status, _, previous = request(port, path, cookie=session)
        assert status == 200
        form["mittab_list_revision"] = list_edit_proof(previous)
    if configuration_revision and form is not None:
        status, _, previous = request(port, path, cookie=session)
        assert status == 200
        form["mittab_configuration_revision"] = registration_config_proof(previous)
    if registration_revision and form is not None:
        status, _, previous = request(port, path, cookie=session)
        assert status == 200
        form["mittab_registration_revision"] = registration_edit_proof(previous)
    if pairing_revision and form is not None:
        status, _, previous = request(port, path, cookie=session)
        assert status == 200
        form["mittab_pairing_revision"] = admin_edit_proof(previous)
    status, headers, body = request(
        port, path, extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
        method="POST" if form is not None else "GET", form=form,
    )
    token = re.search(rb'name="csrfmiddlewaretoken" value="([A-Za-z0-9]{64})"', body)
    mask = token[1][:32].decode() if token else csrf
    with patch("django.middleware.csrf._get_new_csrf_string", return_value=mask):
        reference = client.post(path, form) if form is not None else client.get(path)
    assert status == reference.status_code, (path, status, reference.status_code)
    if status == 302:
        assert headers["Location"] == reference["Location"], path
    else:
        if deletion_scope:
            body, metadata = re.subn(rb'<input type="hidden" name="mittab_delete_scope" value="[a-f0-9]{64}">', b"", body)
            assert metadata == 1
        if pairing_revision:
            body, metadata = re.subn(rb'<input type="hidden" name="mittab_pairing_revision" value="[a-f0-9]{64}">', b"", body)
            assert metadata == 1
        if registration_revision:
            body, metadata = re.subn(
                rb'<input type="hidden" name="mittab_registration_revision" value="[a-f0-9]{64}">', b"", body,
            )
            assert metadata == 1
            body, metadata = re.subn(
                rb'<input type="hidden" name="mittab_registration_delete_revision" value="[a-f0-9]{64}">', b"", body,
            )
            assert metadata == 1
        if configuration_revision:
            body, metadata = re.subn(
                rb'<input type="hidden" name="mittab_configuration_revision" value="[a-f0-9]{64}">', b"", body,
            )
            assert metadata == 1
        if staff_motion_revision:
            body, metadata = re.subn(
                rb'<input type="hidden" name="motion_revision" value="[a-f0-9]{64}">', b"", body,
            )
            assert metadata >= 1
        if motion_revision:
            body, metadata = re.subn(
                rb'<input type="hidden" name="mittab_motion_revision" value="[a-f0-9]{64}">', b"", body,
            )
            assert metadata == 1
        if content_revision:
            body, metadata = re.subn(
                rb'<input type="hidden" name="mittab_content_revision" value="[a-f0-9]{64}">', b"", body,
            )
            assert metadata == 1
        if list_revision:
            body, metadata = re.subn(
                rb'<input type="hidden" name="mittab_list_revision" value="[a-f0-9]{64}" id="id_mittab_list_revision">', b"", body,
            )
            assert metadata == 1
        assert_html_bytes(body, reference.content)


def staff_motion_proof(body, action=None):
    if action:
        actions = re.findall(rb'<form[^>]*action="([^"]+)"[^>]*>(.*?)</form>', body, flags=re.DOTALL)
        body = next(form for url, form in actions if url.decode() == action)
    return re.search(rb'name="motion_revision" value="([a-f0-9]{64})"', body)[1].decode()


def motion_delete_proof(body):
    return re.search(rb'name="mittab_delete_scope" value="([a-f0-9]{64})"', body)[1].decode()


def motion_edit_proof(body):
    return re.search(rb'name="mittab_motion_revision" value="([a-f0-9]{64})"', body)[1].decode()


def content_edit_proof(body):
    return re.search(rb'name="mittab_content_revision" value="([a-f0-9]{64})"', body)[1].decode()


def list_edit_proof(body):
    return re.search(rb'name="mittab_list_revision" value="([a-f0-9]{64})"', body)[1].decode()


def registration_config_proof(body):
    return re.search(rb'name="mittab_configuration_revision" value="([a-f0-9]{64})"', body)[1].decode()


def registration_delete_proof(body):
    return re.search(rb'name="mittab_registration_delete_revision" value="([a-f0-9]{64})"', body)[1].decode()


def registration_edit_proof(body):
    return re.search(rb'name="mittab_registration_revision" value="([a-f0-9]{64})"', body)[1].decode()


@pytest.mark.django_db(transaction=True)
def test_registration_admin_matches_django_and_saves_bound_inlines(html_assets, subtests):
    from django.contrib.admin.models import LogEntry
    from mittab.apps.registration.models import (
        Registration, RegistrationChangeLog, RegistrationConfirmationEmailLog,
    )

    user_model = get_user_model()
    session = legacy_tab_session("registration-admin")
    board = legacy_tab_session("registration-board")
    user_model.objects.get(username="registration-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="Fixture & School")
    other_school = School.objects.create(name='Other "Quoted" School')
    registration = Registration.objects.create(school=school, email="fixture@example.org",
        dietary_requests="Notes < & >", housing_requests="Fixture Housing", herokunator_code="fixture-registration")
    other = Registration.objects.create(school=other_school, email="other@example.org",
        herokunator_code="other-registration")
    team = Team.objects.create(name="Fixture & Team", school=school, seed=1, registration=registration)
    other_team = Team.objects.create(name="Other Team", school=other_school, seed=1, registration=other)
    debater = Debater.objects.create(name="Fixture Debater", school=school, novice_status=0)
    team.debaters.add(debater)
    judge = Judge.objects.create(name="Fixture <Judge>", rank=Decimal("2.50"), reported_experience=5,
                                 registration=registration, email="judge@example.org")
    round_obj = Round.objects.create(round_number=1, gov_team=team, opp_team=other_team, victor=Round.GOV)
    change = RegistrationChangeLog.objects.create(registration=registration, action="created",
        summary="Created test registration\nOne <team>.", email=registration.email, school_name=school.name,
        registration_code=registration.herokunator_code)
    email = RegistrationConfirmationEmailLog.objects.create(registration=registration,
        email=registration.email, email_payload={"subject": "Fixture"}, successful=False,
        error_message="Delivery failed\r\nCheck <address>.")
    client = Client()
    csrf = "a" * 32
    client.cookies["sessionid"] = session
    client.cookies["csrftoken"] = csrf
    root = "/admin/registration/registration/"
    edit = f"{root}{registration.id}/change/"

    def submit(port, path, fields, sid=session):
        return request(port, path, method="POST", form={**fields, "csrfmiddlewaretoken": csrf},
                       extra_headers={"Cookie": f"sessionid={sid}; csrftoken={csrf}"})

    fields = {
        "school": school.id, "email": registration.email, "dietary_requests": registration.dietary_requests,
        "housing_requests": registration.housing_requests, "herokunator_code": registration.herokunator_code,
        "teams-TOTAL_FORMS": 1, "teams-INITIAL_FORMS": 1,
        "teams-0-id": team.id, "teams-0-registration": registration.id, "teams-0-name": team.name,
        "teams-0-seed": team.seed, "teams-0-school": school.id, "teams-0-hybrid_school": "",
        "judges-TOTAL_FORMS": 1, "judges-INITIAL_FORMS": 1,
        "judges-0-id": judge.id, "judges-0-registration": registration.id, "judges-0-name": judge.name,
        "judges-0-reported_experience": 5, "judges-0-rank": "2.50", "judges-0-email": judge.email,
        "confirmation_email_logs-TOTAL_FORMS": 1, "confirmation_email_logs-INITIAL_FORMS": 1,
        "confirmation_email_logs-0-id": email.id, "confirmation_email_logs-0-registration": registration.id,
        "change_logs-TOTAL_FORMS": 1, "change_logs-INITIAL_FORMS": 1,
        "change_logs-0-id": change.id, "change_logs-0-registration": registration.id,
    }

    for prefix, maximum in (("teams", 1000), ("judges", 1000),
                            ("confirmation_email_logs", 0), ("change_logs", 0)):
        fields[f"{prefix}-MIN_NUM_FORMS"] = 0
        fields[f"{prefix}-MAX_NUM_FORMS"] = maximum

    with rust_server(overrides=html_assets) as port:
        for path in (
            root, root + "?q=Fixture", root + "?o=2", root + "?_popup=1&_to_field=id",
            root + "?" + urlencode({"q": r'"Other \"Quoted\" School"'}),
            root + "add/", f"{root}{other.id}/change/", edit,
            f"{root}{registration.id}/history/",
        ):
            with subtests.test(path=path):
                compare_admin_page(port, path, client, registration_revision=path.endswith("/change/"))
        compare_admin_page(port, edit, client, {**fields, "email": "invalid"}, registration_revision=True)
        compare_admin_page(port, edit, client, {
            **fields, "judges-TOTAL_FORMS": 3,
            "judges-1-name": "Duplicate Judge", "judges-1-rank": "2.00",
            "judges-2-name": "Duplicate Judge", "judges-2-rank": "2.50",
        }, registration_revision=True)
        original_page = request(port, edit, cookie=session)[2]
        initial = registration_edit_proof(original_page)
        initial_delete = registration_delete_proof(original_page)
        added_round = Round.objects.create(round_number=2, gov_team=team, opp_team=other_team)
        assert submit(port, edit, {**fields, "mittab_registration_revision": initial,
                                  "mittab_registration_delete_revision": initial_delete,
                                  "teams-0-DELETE": "on"})[0] == 409
        assert Team.objects.filter(pk=team.id).exists() and Round.objects.filter(pk=added_round.id).exists()
        # A dependency change requires a fresh deletion confirmation. Other edits may proceed.
        assert submit(port, edit, {**fields, "mittab_registration_revision": initial,
                                  "email": "updated-metadata@example.org"})[0] == 302
        added_round.delete()
        form = {**fields, "mittab_registration_revision": registration_edit_proof(
            request(port, edit, cookie=session)[2],
        )}
        assert request(port, edit, cookie=board)[1]["Location"] == "/403/"
        assert submit(port, edit, form, sid=board)[1]["Location"] == "/403/"
        status, _, body = submit(port, edit, {**form, "teams-0-id": other_team.id, "email": "forged@example.org"})
        assert status == 200 and b"The submitted rows do not match this registration." in body
        assert Registration.objects.get(pk=registration.pk).email == "updated-metadata@example.org"
        assert Team.objects.get(pk=other_team.pk).registration_id == other.id

        status, _, body = submit(port, edit, {**form, "judges-0-rank": "bad", "teams-0-name": "No partial save"})
        assert status == 200 and b"Enter a number." in body
        assert Team.objects.get(pk=team.pk).name == team.name
        assert submit(port, edit, {
            **form, "email": "updated@example.org", "teams-0-seed": 2,
            "judges-TOTAL_FORMS": 2, "judges-0-name": "Updated Judge", "judges-1-name": "New Judge", "judges-1-rank": "3.00",
        })[0] == 302
        registration.refresh_from_db()
        team.refresh_from_db()
        assert registration.email == "updated@example.org"
        assert team.seed == 2 and team.debaters.filter(pk=debater.pk).exists()
        new_judge = Judge.objects.get(registration=registration, name="New Judge")
        assert new_judge.rank == Decimal("3.00") and new_judge.ballot_code and new_judge.created_at
        assert submit(port, edit, form)[0] == 409
        assert LogEntry.objects.filter(content_type__app_label="registration", object_id=str(registration.id),
                                       action_flag=2).exists()
        compare_admin_page(port, edit, client, registration_revision=True)
        compare_admin_page(port, f"{root}{registration.id}/history/", client)
        delete_path = f"{root}{registration.id}/delete/"
        compare_admin_page(port, delete_path, client, deletion_scope=True)
        status, _, body = request(port, delete_path, cookie=session)
        assert status == 200 and b"Fixture &amp; Team" in body and b"Registration confirmation failed:" in body
        scope = re.search(rb'name="mittab_delete_scope" value="([a-f0-9]{64})"', body)[1].decode()
        late_change = RegistrationChangeLog.objects.create(registration=registration, action="updated")
        assert submit(port, delete_path, {"post": "yes", "mittab_delete_scope": scope})[0] == 409
        assert RegistrationChangeLog.objects.get(pk=late_change.pk).registration_id == registration.id
        scope = re.search(rb'name="mittab_delete_scope" value="([a-f0-9]{64})"',
                          request(port, delete_path, cookie=session)[2])[1].decode()
        assert submit(port, delete_path, {"post": "yes", "mittab_delete_scope": scope})[0] == 302
        assert Registration.objects.filter(pk=other.pk).exists()
        assert not Registration.objects.filter(pk=registration.pk).exists()
        assert not Round.objects.filter(pk=round_obj.pk).exists()
        assert not Judge.objects.filter(pk__in=(judge.pk, new_judge.pk)).exists()
        assert not RegistrationConfirmationEmailLog.objects.filter(pk=email.pk).exists()
        assert RegistrationChangeLog.objects.get(pk=change.pk).registration_id is None
        assert Debater.objects.filter(pk=debater.pk).exists()
        assert Team.objects.filter(pk=other_team.pk).exists()
        assert submit(port, root + "?q=NoSuchRegistration", {
            "action": "delete_selected", "_selected_action": other.id, "index": "0",
        })[0] == 302
        assert Registration.objects.filter(pk=other.pk).exists()
        add_form = {
            "school": other_school.id, "email": "new@example.org",
            "teams-TOTAL_FORMS": 1, "teams-INITIAL_FORMS": 0,
            "teams-0-name": "New Team", "teams-0-school": other_school.id, "teams-0-seed": 0,
            "judges-TOTAL_FORMS": 0, "judges-INITIAL_FORMS": 0,
            "confirmation_email_logs-TOTAL_FORMS": 0, "confirmation_email_logs-INITIAL_FORMS": 0,
            "change_logs-TOTAL_FORMS": 0, "change_logs-INITIAL_FORMS": 0,
        }
        assert submit(port, root + "add/", add_form)[0] == 302
        created = Registration.objects.get(email=add_form["email"])
        created_team = created.teams.get()
        assert created_team.created_at and created_team.checked_in and created_team.team_code
        assert created.herokunator_code and created.created_at
        created_path = f"{root}{created.id}/change/"
        created_page = request(port, created_path, cookie=session)[2]
        created_form = {**add_form, "herokunator_code": created.herokunator_code,
                        "teams-INITIAL_FORMS": 1, "teams-0-id": created_team.id, "teams-0-DELETE": "on",
                        "mittab_registration_revision": registration_edit_proof(created_page),
                        "mittab_registration_delete_revision": registration_delete_proof(created_page)}
        assert submit(port, created_path, created_form)[0] == 302
        assert not created.teams.exists() and Registration.objects.filter(pk=created.id).exists()


@pytest.mark.django_db(transaction=True)
def test_registration_admin_config_and_logs_match_django(html_assets, subtests):
    from django.contrib.admin.models import LogEntry
    from mittab.apps.registration.models import (
        Registration, RegistrationChangeLog, RegistrationConfirmationEmailLog,
    )

    session = legacy_tab_session("registration-config-admin")
    board = legacy_tab_session("registration-config-board")
    get_user_model().objects.get(username="registration-config-board").groups.add(
        Group.objects.create(name="APDA Board"),
    )
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="A & B School")
    gov, opp = [Team.objects.create(name=name, school=school, seed=0) for name in ("Gov", "Opp")]
    Round.objects.create(round_number=1, gov_team=gov, opp_team=opp, victor=Round.GOV)
    registration = Registration.objects.create(school=school, email="fixture@example.org")
    change = RegistrationChangeLog.objects.create(
        registration=registration, registration_code=registration.herokunator_code, school_name=school.name,
        email=registration.email, action="updated", summary="An edit.\nA second line <and tags>.",
        changes={"a": [1, {"escaped": "café", "longer": True}]}, snapshot={"note": "☕"},
    )
    detached = RegistrationChangeLog.objects.create(
        action="deleted", summary="Removed", registration_code="former-tournament",
    )
    sent = RegistrationConfirmationEmailLog.objects.create(
        registration=registration, email="sent@example.org", successful=True,
        email_payload={"z": "Résumé 💬", "subject": "Fixture", "body": ["Evidence"]},
    )
    failed = RegistrationConfirmationEmailLog.objects.create(
        registration=registration, email="failed@example.org", successful=False,
        error_message="Address excluded.\nCheck the recipient <configuration>.",
    )
    RegistrationConfig.objects.all().delete()
    client = Client()
    csrf = "a" * 32
    client.cookies["sessionid"] = session
    client.cookies["csrftoken"] = csrf
    root = "/admin/registration/"
    configs = root + "registrationconfig/"
    changes = root + "registrationchangelog/"
    emails = root + "registrationconfirmationemaillog/"
    config_edit = configs + "1/change/"
    change_edit = f"{changes}{change.id}/change/"
    sent_edit = f"{emails}{sent.id}/change/"
    failed_edit = f"{emails}{failed.id}/change/"

    def submit(port, path, fields, sid=session):
        return request(port, path, method="POST", form={**fields, "csrfmiddlewaretoken": csrf},
                       extra_headers={"Cookie": f"sessionid={sid}; csrftoken={csrf}"})

    schedule = {
        "allow_new_registrations": "on", "allow_registration_edits": "on",
        "new_registrations_open_at": "2026-01-17T09:15", "new_registrations_close_at": "",
        "registration_edits_open_at": "2026-07-13T15:30", "registration_edits_close_at": "2026-07-14T23:00",
        "team_name_changes_allowed": "on", "disc_scratch_quantity": 3, "_continue": "Save and continue editing",
    }

    with rust_server(overrides=html_assets) as port:
        for path in (
            configs, configs + "add/", changes, changes + "?q=Removed", changes + "?o=3",
            change_edit, f"{changes}{detached.id}/change/", f"{changes}{detached.id}/delete/",
            emails, emails + "?successful__exact=0", emails + "?q=excluded&o=-3",
            sent_edit, failed_edit, f"{emails}{sent.id}/history/", f"{emails}{sent.id}/delete/",
        ):
            with subtests.test(path=path):
                compare_admin_page(port, path, client)
        for path in (changes + "add/", emails + "add/"):
            assert request(port, path, cookie=session)[0] == client.get(path).status_code == 403
            assert submit(port, path, {"email_payload": "{}"})[0] == 403
        for path in (configs + "add/", change_edit, sent_edit):
            assert request(port, path, cookie=board)[1]["Location"] == "/403/"
            assert submit(port, path, schedule, sid=board)[1]["Location"] == "/403/"

        for text in ("2026-03-08T02:30", "2026-11-01T01:30", "invalid"):
            with subtests.test(eastern_time=text):
                compare_admin_page(port, configs + "add/", client, {
                    **schedule, "new_registrations_open_at": text,
                })
        compare_admin_page(port, configs + "add/", client, {**schedule, "disc_scratch_quantity": -1})
        assert RegistrationConfig.get_active() is None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [executor.submit(submit, port, configs + "add/", schedule) for _ in range(2)]
            assert sorted(future.result(timeout=15)[0] for future in results) == [302, 403]
        config = RegistrationConfig.get_active()
        assert config.pk == 1 and config.disc_scratch_quantity == 3
        assert config.new_registrations_open_at == datetime.fromisoformat("2026-01-17T14:15:00+00:00")
        assert config.registration_edits_open_at == datetime.fromisoformat("2026-07-13T19:30:00+00:00")
        assert config.registration_edits_close_at == datetime.fromisoformat("2026-07-15T03:00:00+00:00")
        assert request(port, configs + "add/", cookie=session)[0] == 403
        config_delete = configs + "1/delete/"
        for path in (configs, config_edit, configs + "1/history/", config_delete):
            with subtests.test(path=path):
                compare_admin_page(port, path, client, configuration_revision=path == config_edit,
                                   deletion_scope=path == config_delete)

        old_edit = registration_config_proof(request(port, config_edit, cookie=session)[2])
        old_delete = re.search(rb'name="mittab_delete_scope" value="([a-f0-9]{64})"',
                               request(port, config_delete, cookie=session)[2])[1].decode()
        config.delete()
        assert submit(port, configs + "add/", schedule)[0] == 302
        config = RegistrationConfig.get_active()
        assert submit(port, config_edit, {**schedule, "mittab_configuration_revision": old_edit})[0] == 409
        assert submit(port, config_delete, {"post": "yes", "mittab_delete_scope": old_delete})[0] == 409
        config_edit_proof = registration_config_proof(request(port, config_edit, cookie=session)[2])
        assert submit(port, config_edit, {
            **schedule, "crash_housing_enabled": "on", "disc_scratch_quantity": 4,
            "mittab_configuration_revision": config_edit_proof,
        })[0] == 302
        config.refresh_from_db()
        assert config.crash_housing_enabled and config.disc_scratch_quantity == 4
        compare_admin_page(port, configs + "1/history/", client)
        instant = datetime.fromisoformat("2026-11-01T06:15:47.432101+00:00")
        RegistrationConfig.objects.filter(pk=1).update(registration_edits_open_at=instant, disc_scratch_quantity=65535)
        precise = request(port, config_edit, cookie=session)[2]
        assert b'value="2026-11-01T01:15"' in precise
        assert submit(port, config_edit, {
            **schedule, "registration_edits_open_at": "2026-11-01T01:15", "disc_scratch_quantity": 65535,
            "mittab_configuration_revision": registration_config_proof(precise),
        })[0] == 302
        config.refresh_from_db()
        assert config.registration_edits_open_at == instant and config.disc_scratch_quantity == 65535

        previous_payload = sent.email_payload
        assert submit(port, sent_edit, {"email": "spoofed@example.org", "_continue": "Save"})[0] == 302
        sent.refresh_from_db()
        assert sent.email_payload == previous_payload
        compare_admin_page(port, failed_edit, client, {"email_payload": '{"unfinished":', "initial-email_payload": "{}"})
        assert submit(port, sent_edit, {
            "email_payload": '{"subject": "Edited note", "flag": true}',
            "email": "spoofed@example.org", "registration": str(registration.id + 1),
            "successful": "0", "sent_at": "1990-01-07", "error_message": "forged",
        })[0] == 302
        sent.refresh_from_db()
        assert sent.email_payload == {"subject": "Edited note", "flag": True}
        assert sent.email == "sent@example.org" and sent.registration_id == registration.id
        assert sent.successful and sent.error_message == ""
        compare_admin_page(port, f"{emails}{sent.id}/history/", client)

        assert submit(port, change_edit, {"summary": "forged", "action": "deleted"})[0] == 302
        change.refresh_from_db()
        assert change.action == "updated" and "An edit." in change.summary
        assert LogEntry.objects.filter(content_type__app_label="registration",
                                       content_type__model="registrationchangelog", object_id=str(change.id)).exists()

        filtered = emails + "?successful__exact=0"
        compare_admin_page(port, filtered, client, {
            "action": "delete_selected", "index": 0, "select_across": 1,
            "_selected_action": [str(sent.id), str(failed.id)],
        })
        assert submit(port, filtered, {
            "action": "delete_selected", "post": "yes", "_selected_action": [str(sent.id), str(failed.id)],
        })[0] == 302
        assert RegistrationConfirmationEmailLog.objects.filter(pk=sent.pk).exists()
        assert not RegistrationConfirmationEmailLog.objects.filter(pk=failed.pk).exists()
        assert Registration.objects.filter(pk=registration.pk).exists()
        config_scope = re.search(rb'name="mittab_delete_scope" value="([a-f0-9]{64})"',
                                 request(port, config_delete, cookie=session)[2])[1].decode()
        assert submit(port, config_delete, {"post": "yes", "mittab_delete_scope": config_scope})[0] == 302
        assert RegistrationConfig.get_active() is None


@pytest.mark.django_db(transaction=True)
def test_registration_and_public_information_admin_link_parity(html_assets, subtests):
    from django.contrib.admin.models import LogEntry
    from mittab.apps.registration.models import RegistrationLink

    session = legacy_tab_session("registration-link-admin")
    board = legacy_tab_session("registration-link-board")
    get_user_model().objects.get(username="registration-link-board").groups.add(
        Group.objects.create(name="APDA Board"),
    )
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="Public Info School")
    gov, opp = [Team.objects.create(name=name, school=school, seed=0) for name in ("Gov", "Opp")]
    Round.objects.create(round_number=1, gov_team=gov, opp_team=opp, victor=Round.GOV)
    csrf = "a" * 32
    client = Client()
    client.cookies["sessionid"] = session
    client.cookies["csrftoken"] = csrf

    def submit(port, path, fields, sid=session):
        return request(port, path, method="POST", form={**fields, "csrfmiddlewaretoken": csrf},
                       extra_headers={"Cookie": f"sessionid={sid}; csrftoken={csrf}"})

    with rust_server(overrides=html_assets) as port:
        for model in (InfoLink, InfoText, RegistrationLink):
            with subtests.test(model=model._meta.model_name):
                root = f"/admin/registration/{model._meta.model_name}/"
                model.objects.all().delete()
                compare_admin_page(port, root, client, list_revision=True)
                compare_admin_page(port, root + "add/", client)
                if model is InfoText:
                    content = {"text": "Tournament notes <&>"}
                    invalid = {"text": ""}
                else:
                    content = {"url": "https://example.org/page?one=1&two=2", "description": ""}
                    invalid = {"url": "javascript:alert(1)"}
                first = model.objects.create(title="Example & Information", display_order=0, **content)
                other = model.objects.create(title="Other information", display_order=1, **content)

                for suffix in ("", "?q=Example", "?o=-4", "?q=absent", "?_popup=1&_to_field=id"):
                    compare_admin_page(port, root + suffix, client, list_revision="_popup" not in suffix)
                for action in ("change", "history", "delete"):
                    compare_admin_page(port, f"{root}{first.id}/{action}/", client, content_revision=action == "change")
                assert request(port, root, cookie=board)[1]["Location"] == "/403/"

                add_form = {"title": "New public information", "display_order": 8, **content, "is_active": "on"}
                compare_admin_page(port, root + "add/", client, {**add_form, **invalid})
                compare_admin_page(port, root + "add/", client, {**add_form, "display_order": -1})
                assert submit(port, root + "add/", add_form)[0] == 302
                added = model.objects.get(title=add_form["title"])
                assert added.is_active and added.display_order == 8
                added_path = f"{root}{added.id}/change/"

                def submit_detail(path, fields):
                    proof = content_edit_proof(request(port, path, cookie=session)[2])
                    return submit(port, path, {**fields, "mittab_content_revision": proof})

                assert submit_detail(added_path, {**add_form, "display_order": 4294967295})[0] == 302
                added.refresh_from_db()
                assert added.display_order == 4294967295
                compare_admin_page(port, f"{root}{added.id}/history/", client)
                if model is InfoText:
                    staff_form = f"/registration/manage/info-text/{added.id}/"
                else:
                    kind = "info" if model is InfoLink else "registration"
                    staff_form = f"/registration/manage/links/{kind}/{added.id}/"
                staff_title = "Title changed from management"
                status, headers, _ = submit(port, staff_form, {
                    **add_form, "title": staff_title, "display_order": 4294967295,
                })
                assert status == 302 and headers["Location"].endswith("?saved=3")
                added.refresh_from_db()
                assert added.title == staff_title and added.display_order == 4294967295
                add_form["title"] = staff_title
                if model is not InfoText:
                    assert submit_detail(added_path, {**add_form, "display_order": 4294967295})[0] == 302
                    assert LogEntry.objects.filter(content_type__model=model._meta.model_name,
                                                  object_id=str(added.id), action_flag=2).first().change_message == "[]"
                    for bad_url in ("data:text/html,hi", "https:example.org", "https:///example.org",
                                    "https://127.1", "https://example.123a", r"https://account\@example.org"):
                        status, _, body = submit_detail(added_path, {**add_form, "url": bad_url})
                        assert status == 200 and b"Enter a valid URL." in body
                    added.refresh_from_db()
                    assert added.url == content["url"]

                only_first = root + "?" + urlencode({"q": '"Example &"'})
                form = {
                    "form-TOTAL_FORMS": 1, "form-INITIAL_FORMS": 1,
                    "form-MIN_NUM_FORMS": 0, "form-MAX_NUM_FORMS": 1000,
                    "form-0-id": first.id, "form-0-display_order": -1, "_save": "Save",
                }
                compare_admin_page(port, only_first, client, form, list_revision=True)
                proof = list_edit_proof(request(port, only_first, cookie=session)[2])
                form.update({"mittab_list_revision": proof, "form-0-display_order": 6})
                assert submit(port, only_first, form, sid=board)[1]["Location"] == "/403/"
                assert submit(port, only_first, {**form, "form-0-id": other.id})[0] == 409
                assert model.objects.get(id=other.id).display_order == 1
                model.objects.filter(id=first.id).update(title="Example & updated concurrently")
                status, _, body = submit(port, only_first, form)
                assert status == 409 and b"This list changed." in body
                first.refresh_from_db()
                assert first.is_active and first.display_order == 0

                first_path = f"{root}{first.id}/change/"
                old_detail = content_edit_proof(request(port, first_path, cookie=session)[2])
                form["mittab_list_revision"] = list_edit_proof(request(port, only_first, cookie=session)[2])
                status, headers, _ = submit(port, only_first, {**form, "form-0-title": "Forged title"})
                assert status == 302 and headers["Location"] == only_first
                notice = load_cookies(headers)["mittab_admin_notice"].value
                cookies = {"Cookie": f"sessionid={session}; csrftoken={csrf}; mittab_admin_notice={notice}"}
                body = request(port, only_first, extra_headers=cookies)[2]
                assert f"1 {model._meta.verbose_name} was changed successfully.".encode() in body
                first.refresh_from_db()
                assert first.display_order == 6 and not first.is_active
                assert first.title == "Example & updated concurrently"
                assert submit(port, first_path, {
                    **add_form, "mittab_content_revision": old_detail,
                })[0] == 409
                assert not model.objects.get(id=first.id).is_active
                compare_admin_page(port, f"{root}{first.id}/history/", client)
                assert LogEntry.objects.filter(content_type__model=model._meta.model_name,
                                              object_id=str(first.id), action_flag=2).count() == 1

                all_rows = {
                    "form-TOTAL_FORMS": 3, "form-INITIAL_FORMS": 3,
                    "form-MIN_NUM_FORMS": 0, "form-MAX_NUM_FORMS": 1000,
                    "form-0-id": other.id, "form-0-display_order": 2, "form-0-is_active": "on",
                    "form-1-id": first.id, "form-1-display_order": -1,
                    "form-2-id": added.id, "form-2-display_order": 4294967295, "form-2-is_active": "on",
                    "mittab_list_revision": list_edit_proof(request(port, root, cookie=session)[2]),
                    "_save": "Save",
                }
                assert submit(port, root, all_rows)[0] == 200
                assert model.objects.get(id=other.id).display_order == 1
                status, headers, _ = submit(port, root, {**all_rows, "form-1-display_order": 7})
                assert status == 302 and headers["Location"] == root
                notice = load_cookies(headers)["mittab_admin_notice"].value
                cookies = {"Cookie": f"sessionid={session}; csrftoken={csrf}; mittab_admin_notice={notice}"}
                body = request(port, root, extra_headers=cookies)[2]
                assert f"2 {model._meta.verbose_name_plural} were changed successfully.".encode() in body
                assert model.objects.get(id=other.id).display_order == 2
                assert model.objects.get(id=first.id).display_order == 7
                assert model.objects.get(id=added.id).display_order == 4294967295

                assert submit(port, f"{root}{first.id}/delete/", {"post": "yes"})[0] == 302
                assert not model.objects.filter(id=first.id).exists()
                assert model.objects.filter(id=other.id).exists()
                if model is not InfoText:
                    assert model.objects.get(id=added.id).url == content["url"]


@pytest.mark.django_db(transaction=True)
def test_motion_admin_matches_django_and_publishes_current_motion(html_assets, subtests):
    session = legacy_tab_session("motions-admin")
    board = legacy_tab_session("motions-board")
    get_user_model().objects.get(username="motions-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("motions_enabled", 1)
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="Motion School")
    gov, opp = [Team.objects.create(name=name, school=school, seed=0) for name in ("Gov", "Opp")]
    Round.objects.create(round_number=1, gov_team=gov, opp_team=opp, victor=Round.GOV)
    preliminary = Motion.objects.create(
        round_number=2, motion_text="This house would protect the coastline. " + "x\u030b" * 112,
        info_slide="Only in this folder <&>.", is_published=True,
    )
    varsity = Motion.objects.create(outround_type=Motion.VARSITY, num_teams=4, motion_text="Varsity fixture motion " + "x" * 60)
    novice = Motion.objects.create(
        outround_type=Motion.NOVICE, num_teams=64, motion_text="Novice fixture motion", is_published=True,
    )
    Motion.objects.create(motion_text="Unassigned fixture motion")
    root = "/admin/tab/motion/"
    edit = f"{root}{preliminary.pk}/change/"
    csrf = "a" * 32
    client = Client()
    client.cookies["sessionid"] = session
    client.cookies["csrftoken"] = csrf

    def submit(port, path, fields, sid=session):
        return request(port, path, method="POST", form={**fields, "csrfmiddlewaretoken": csrf},
                       extra_headers={"Cookie": f"sessionid={sid}; csrftoken={csrf}"})

    valid = {"round_number": "3", "outround_type": "", "num_teams": "",
             "motion_text": "This house would subsidize ferries <and buses>.", "info_slide": "Second <line>.",
             "is_published": "on"}
    with rust_server(overrides=html_assets) as port:
        for suffix in ("", "?q=coastline", "?q=folder", "?q=missing", "?o=-3", "?is_published__exact=1",
                       "?outround_type__exact=1", "?is_published__exact=1&outround_type__isnull=True",
                       "?_popup=1&_to_field=id", "add/", f"{preliminary.pk}/change/", f"{novice.pk}/change/",
                       f"{preliminary.pk}/history/", f"{varsity.pk}/delete/"):
            with subtests.test(suffix=suffix):
                compare_admin_page(port, root + suffix, client, motion_revision=suffix.endswith("/change/"),
                                   deletion_scope=suffix.endswith("/delete/"))
        for fields in (
            {},
            {**valid, "motion_text": ""},
            {**valid, "num_teams": 4, "outround_type": "0"},
            {**valid, "round_number": "", "num_teams": 4},
            {**valid, "round_number": "", "num_teams": ""},
            {**valid, "outround_type": "unknown", "round_number": "word"},
        ):
            with subtests.test(fields=fields):
                compare_admin_page(port, root + "add/", client, fields)

        staff_edit = f"/motions/{preliminary.pk}/edit/"
        toggle = f"/motions/{preliminary.pk}/toggle-published/"
        publish_all = "/motions/publish-all/"
        compare_admin_page(port, "/motions/", client, staff_motion_revision=True)
        compare_admin_page(port, staff_edit, client, staff_motion_revision=True)
        compare_admin_page(port, staff_edit, client, {
            "round_selection": "outround_1_8", "motion_text": "", "info_slide": "Preserved",
        }, staff_motion_revision=True)

        assert request(port, root, cookie=board)[1]["Location"] == "/403/"
        assert submit(port, edit, valid, sid=board)[1]["Location"] == "/403/"
        assert request(port, "/public/motions/")[0] == 200
        assert b"protect the coastline" in request(port, "/public/motions/")[2]

        proof = motion_edit_proof(request(port, edit, cookie=session)[2])
        staff_page = request(port, "/motions/", cookie=session)[2]
        old_toggle = staff_motion_proof(staff_page, toggle)
        old_bulk = staff_motion_proof(staff_page, publish_all)
        old_staff = staff_motion_proof(request(port, staff_edit, cookie=session)[2])
        Motion.objects.filter(pk=preliminary.pk).update(is_published=False)
        assert submit(port, edit, {**valid, "mittab_motion_revision": proof})[0] == 409
        preliminary.refresh_from_db()
        assert not preliminary.is_published and preliminary.round_number == 2
        assert submit(port, toggle, {"motion_revision": old_toggle})[0] == 409
        stale_form = {"motion_revision": old_staff, "round_selection": "inround_2"}
        assert submit(port, staff_edit, {**stale_form, "motion_text": ""})[0] == 409
        assert submit(port, staff_edit, {**stale_form, "motion_text": "Stale staff content"})[0] == 409
        late_draft = Motion.objects.create(round_number=1, motion_text="Private until reviewed")
        assert submit(port, publish_all, {"motion_revision": old_bulk})[0] == 409
        assert not Motion.objects.get(pk=late_draft.pk).is_published
        late_draft.delete()

        assert b"protect the coastline" not in request(port, "/public/motions/")[2]
        proof = motion_edit_proof(request(port, edit, cookie=session)[2])
        assert submit(port, edit, {**valid, "mittab_motion_revision": proof})[0] == 302
        preliminary.refresh_from_db()
        assert preliminary.is_published and preliminary.round_number == 3
        body = request(port, "/public/motions/")[2]
        assert b"subsidize ferries &lt;and buses&gt;" in body and b"protect the coastline" not in body
        compare_admin_page(port, edit, client, motion_revision=True)
        compare_admin_page(port, f"{root}{preliminary.pk}/history/", client)

        other_fields = {**valid, "motion_text": "New varsity motion", "round_number": "",
                        "num_teams": 12, "outround_type": "0", "is_published": "off"}
        assert submit(port, root + "add/", other_fields)[0] == 302
        added = Motion.objects.get(motion_text=other_fields["motion_text"])
        assert added.num_teams == 12 and not added.is_published
        path = root + "?outround_type__exact=0"
        preview = {
            "action": "delete_selected", "index": 0, "select_across": 1,
            "_selected_action": [str(added.pk), str(varsity.pk), str(novice.pk)],
        }
        compare_admin_page(port, path, client, preview, deletion_scope=True)
        old_scope = motion_delete_proof(submit(port, path, preview)[2])
        Motion.objects.filter(pk=varsity.pk).update(motion_text=varsity.motion_text + "revised ending")
        confirmation = {"action": "delete_selected", "post": "yes", "_selected_action": preview["_selected_action"]}
        assert submit(port, path, {**confirmation, "mittab_delete_scope": old_scope})[0] == 409
        scope = motion_delete_proof(submit(port, path, preview)[2])
        assert submit(port, path, {**confirmation, "mittab_delete_scope": scope})[0] == 302
        assert not Motion.objects.filter(pk__in=(added.pk, varsity.pk)).exists()
        assert Motion.objects.filter(pk=novice.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_native_administration_manages_accounts_groups_and_audit_with_board_exclusion():
    from django.contrib.auth.hashers import check_password
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType
    from mittab.apps.tab.models import PublishedOutroundStage

    staff = legacy_tab_session("native-admin")
    board = legacy_tab_session("native-admin-board")
    user_model = get_user_model()
    board_group = Group.objects.create(name="APDA Board")
    user_model.objects.get(username="native-admin-board").groups.add(board_group)
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="Admin School")
    teams = [Team.objects.create(name=f"Admin Team {i}", school=school, seed=0) for i in (1, 2)]
    debate = Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1], victor=Round.GOV)
    audit = AuditEvent.objects.create(content_type=ContentType.objects.get_for_model(Round), object_id=debate.id,
                                      object_repr="Fixture audit record", event_type="updated",
                                      changes={"field": "fixture evidence"}, note="Staff audit entry")
    assert rust_command("migrate", connection.settings_dict["NAME"]).returncode == 0
    permission = Permission.objects.get(codename="change_publicrosterchange")
    users = "/admin/auth/user/"
    groups = "/admin/auth/group/"
    record = f"/admin/tab/auditevent/{audit.id}/change/"
    with rust_server() as port:
        for path in ("/admin", "/admin/", users, groups, record, "/admin/unknown/"):
            assert request(port, path, cookie=board)[1]["Location"] == "/403/"
        assert request(port, "/admin/")[1]["Location"] == "/public/login/?next=/admin/"
        assert request(port, "/admin", cookie=staff)[1]["Location"] == "/admin/"
        assert request(port, "/admin/unknown/", cookie=staff)[0] == 404
        assert request(port, "/404/")[0] == 404 and request(port, "/500/")[0] == 500
        status, _, team_list = request(port, "/admin/tab/team/", cookie=staff)
        assert status == 200 and b"Select team to change" in team_list and b"Admin Team 1" in team_list
        status, _, page = request(port, "/admin/tab/auditevent/?q=Fixture", cookie=staff)
        assert status == 200 and b"Fixture audit record" in page, page
        assert b"fixture evidence" in request(port, record, cookie=staff)[2]
        status, headers, page = request(port, f"{groups}add/", cookie=staff)
        assert status == 200 and b"Can change public roster change" in page, page
        csrf = load_cookies(headers)["csrftoken"].value

        def post(path, fields, session=staff):
            return request(port, path, method="POST",
                           extra_headers={"Cookie": f"sessionid={session}; csrftoken={csrf}"},
                           form={"csrfmiddlewaretoken": csrf, **fields})

        assert post(f"{groups}add/", {"name": "Fixture Group", "permissions": permission.id})[0] == 302
        group = Group.objects.get(name="Fixture Group")
        assert set(group.permissions.all()) == {permission}
        entry = {"username": "fixture-user", "active": "on", "staff": "on", "groups": group.id,
                 "first_name": "Fixture", "email": "fixture-account@example.invalid",
                 "password": "fixture-user-password", "confirmation": "fixture-user-password"}
        assert post(f"{users}add/", {**entry, "groups": 99999999})[0] == 422
        assert not user_model.objects.filter(username="fixture-user").exists()
        assert post(f"{users}add/", entry, board)[1]["Location"] == "/403/"
        response = post(f"{users}add/", entry)
        assert response[0] == 302 and response[1]["Location"] == users, response
        user = user_model.objects.get(username="fixture-user")
        assert check_password(entry["password"], user.password)
        assert list(user.groups.all()) == [group]
        assert b"fixture-account@example.invalid" in request(port, users + "?q=fixture-user", cookie=staff)[2]
        assert user.password.encode() not in request(port, f"{users}{user.id}/change/", cookie=staff)[2]
        response = post(f"{users}{user.id}/password/", {"password": "changed-fixture-password",
                                                       "confirmation": "changed-fixture-password"})
        assert response[0] == 302, response
        user.refresh_from_db()
        assert check_password("changed-fixture-password", user.password)
        assert post(f"{groups}{board_group.id}/delete/", {"confirm": "on"})[0] == 422
        assert post(f"{users}{user.id}/delete/", {})[0] == 422
        for deletion in (f"{users}{user.id}/delete/", f"{groups}{group.id}/delete/"):
            status, _, preview = request(port, deletion, cookie=staff)
            assert status == 200
            assert post(deletion, {"post": "yes", "mittab_delete_scope": admin_delete_proof(preview)})[0] == 302
        assert not user_model.objects.filter(id=user.id).exists() and not Group.objects.filter(id=group.id).exists()

        speaker = Debater.objects.create(name="Administrative Score", novice_status=0)
        teams[0].debaters.add(speaker)
        score = RoundStats.objects.create(round=debate, debater=speaker, speaks=25, ranks=1, debater_role="pm")
        edit = f"/admin/tab/roundstats/{score.id}/change/"
        delete = f"/admin/tab/round/{debate.id}/delete/"
        final = Outround.objects.create(num_teams=2, type_of_round=0, gov_team=teams[0], opp_team=teams[1])
        delete_final = f"/admin/tab/outround/{final.id}/delete/"
        for path in (edit, delete, delete_final):
            assert request(port, path, cookie=staff)[0] == 200
            assert request(port, path, cookie=board)[1]["Location"] == "/403/"
            assert post(path, {"confirm": "on"}, board)[1]["Location"] == "/403/"
        fields = {"round": debate.id, "debater": speaker.id, "debater_role": "pm", "speaks": "31.2575", "ranks": "1"}
        add = "/admin/tab/roundstats/add/"
        scores = "/admin/tab/roundstats/"
        history = f"{scores}{score.id}/history/"
        for path in ("/admin/tab/", scores, add, history):
            assert request(port, path, cookie=staff)[0] == 200
            assert request(port, path, cookie=board)[1]["Location"] == "/403/"
        assert post(edit, {**fields, "speaks": "101.0000"})[0] == 422
        assert post(edit, {**fields, "_continue": "Save and continue editing"})[1]["Location"] == edit
        assert RoundStats.objects.get(id=score.id).speaks == Decimal("31.2575")
        assert b"Changed Speaks." in request(port, history, cookie=staff)[2]
        assert AuditEvent.objects.filter(changes__public__source="admin scores", object_id=debate.id).exists()
        assert post(add, fields)[1]["Location"] == scores
        extra = RoundStats.objects.exclude(id=score.id).get(round=debate)
        selected = {"action": "delete_selected", "_selected_action": extra.id}
        confirmation = post(scores, selected)
        assert confirmation[0] == 200 and b"Yes, I" in confirmation[2]
        assert RoundStats.objects.filter(id=extra.id).exists()
        assert post(scores, {**selected, "post": "yes"}, board)[1]["Location"] == "/403/"
        assert post(scores, {**selected, "post": "yes"})[1]["Location"] == scores
        assert not RoundStats.objects.filter(id=extra.id).exists()
        def confirm_admin_delete(path):
            status, _, html = request(port, path, cookie=staff)
            assert status == 200
            proof = admin_delete_proof(html)
            return post(path, {"post": "yes", "mittab_delete_scope": proof})

        assert post(delete, {})[0] == 400
        assert confirm_admin_delete(delete)[0] == 302
        assert not Round.objects.filter(id=debate.id).exists() and not RoundStats.objects.filter(id=score.id).exists()
        assert confirm_admin_delete(delete_final)[0] == 302
        assert not Outround.objects.filter(id=final.id).exists()
        assert AuditEvent.objects.filter(changes__public__source="admin deletion", object_id=final.id,
                                         content_type__model="outround").exists()

        # Deleting an old, hidden semifinal leaves the other published semifinal intact.
        TabSettings.set("var_teams_visible", 256)
        extra = [Team.objects.create(name=f"Admin Semifinal Team {i}", school=school, seed=0)
                 for i in (3, 4)]
        semifinal = Outround.objects.create(num_teams=4, type_of_round=0, gov_team=teams[0], opp_team=teams[1])
        Outround.objects.create(num_teams=4, type_of_round=0, gov_team=extra[0], opp_team=extra[1])
        stage = PublishedOutroundStage.objects.create(type_of_round=0, num_teams=4)
        for team, place in zip(teams + extra, (2, 3, 3, 2)):
            PublishedTeamPlacement.objects.create(stage=stage, team=team, place=place)
        assert confirm_admin_delete(f"/admin/tab/outround/{semifinal.id}/delete/")[0] == 302
        assert dict(PublishedTeamPlacement.objects.filter(stage=stage).values_list("team_id", "place")) == {
            extra[0].id: 3, extra[1].id: 2
        }


@pytest.mark.django_db(transaction=True)
def test_native_maintenance_cli_exports_csv_and_load_tests_local_ballots(tmp_path):
    import csv

    TabSettings.set("cur_round", 2)
    TabSettings.set("pairing_released", 1)
    TabSettings.set("tot_rounds", 1)
    schools = [School.objects.create(name=name) for name in ("CLI École", "CLI Hybrid")]
    teams = [Team.objects.create(name=name, school=schools[0], hybrid_school=schools[1], seed=0)
             for name in ("CLI, Team A", "CLI Team B")]
    for i, team in enumerate(teams):
        team.debaters.add(*[Debater.objects.create(name=f"CLI Speaker {i}-{j}", novice_status=i) for j in (0, 1)])
    judge = Judge.objects.create(name="CLI Judge", rank=4, ballot_code="Local-Load")
    debate = Round.objects.create(round_number=1, gov_team=teams[0], opp_team=teams[1], chair=judge)
    debate.judges.add(judge)
    database = connection.settings_dict["NAME"]
    with rust_server() as port:
        settings = {"MITTAB_ENV": "development"}
        args = ("--host", f"127.0.0.1:{port}", "--connections", "2")
        refused = rust_command("load-test", database, args=args, overrides={"MITTAB_ENV": "production"})
        assert refused.returncode == 1 and "development" in refused.stderr
        remote = rust_command("load-test", database, args=("--host", "example.invalid"), overrides=settings)
        assert remote.returncode == 1 and "loopback" in remote.stderr
        result = rust_command("load-test", database, args=args, overrides=settings)
        assert result.returncode == 0 and "1 of 1" in result.stderr, result.stderr
    debate.refresh_from_db()
    assert debate.victor in (Round.GOV, Round.OPP) and debate.roundstats_set.count() == 4
    result = rust_command("export-stats", database, args=("--root", str(tmp_path), "--team-file", "all.csv"))
    assert result.returncode == 0, result.stderr
    assert sorted(file.name for file in tmp_path.iterdir()) == ["all.csv", "debaters.csv", "nov-debaters.csv", "nov-teams.csv"]
    with (tmp_path / "all.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2 and all(row["School"] == "CLI École" and row["Hyrbid School"] == "CLI Hybrid" for row in rows)
    assert rows[0]["Team Name"] == debate.winner.name
    assert rows[0]["Wins"] == "1" and float(rows[0]["Speaks"]) > 0
    with (tmp_path / "nov-debaters.csv").open(encoding="utf-8", newline="") as handle:
        assert {row["Name"] for row in csv.DictReader(handle)} == {"CLI Speaker 1-0", "CLI Speaker 1-1"}


@pytest.mark.django_db(transaction=True)
def test_native_sentry_reports_route_templates_without_sensitive_request_values():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Event, Thread

    staff = legacy_tab_session("telemetry-staff")
    received = []
    recorded = Event()

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            content = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append(content)
            if b'"type":"event"' in content:
                recorded.set()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

    with ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as collector:
        thread = Thread(target=collector.serve_forever, daemon=True)
        thread.start()
        try:
            env = {
                "SENTRY_DSN": f"http://fixture-key@127.0.0.1:{collector.server_port}/1",
                "SENTRY_ENVIRONMENT": "fixture",
                "SENTRY_TRACES_SAMPLE_RATE": "1",
                "BACKUP_STORAGE": "S3",
                "BACKUP_BUCKET": "",
            }
            with rust_server(overrides=env) as port:
                assert request(port, "/healthz")[0] == 200
                assert request(port, "/backup/download/private-fixture-file/?code=private-fixture-query",
                               cookie=staff)[0] == 503
                assert recorded.wait(5)
        finally:
            collector.shutdown()
            thread.join()
    payload = b"\n".join(received)
    assert b"/backup/download/{key}/" in payload
    assert b"private-fixture-file" not in payload and b"private-fixture-query" not in payload
    assert staff.encode() not in payload


@pytest.mark.django_db(transaction=True)
def test_native_diagnostics_gate_and_javascript_catalog():
    staff = legacy_tab_session("diagnostic-staff")
    board = legacy_tab_session("diagnostic-board")
    get_user_model().objects.get(username="diagnostic-board").groups.add(Group.objects.create(name="APDA Board"))
    TabSettings.set("cur_round", 2)
    TabSettings.set("tot_rounds", 1)
    school = School.objects.create(name="Diagnostic School")
    first = Team.objects.create(name="Diagnostic first", school=school, seed=0)
    second = Team.objects.create(name="Diagnostic second", school=school, seed=0)
    Round.objects.create(round_number=1, gov_team=first, opp_team=second, victor=Round.GOV)
    for environment, code in (("production", 404), ("development", 200)):
        with rust_server(overrides={"SILK_ENABLED": "1", "MITTAB_ENV": environment}) as port:
            path = "/dynamic-media/jsi18n/?ignored=private-fixture-query"
            status, headers, script = request(port, path)
            assert status == 200 and headers["Content-Type"] == 'text/javascript; charset="utf-8"'
            assert b"django.ngettext" in script
            assert request(port, "/silk/", cookie=board)[1]["Location"] == "/403/"
            status, _, body = request(port, "/silk/", cookie=staff)
            assert status == code
            if code == 200:
                assert "/dynamic-media/jsi18n/" in unescape(body.decode())
                assert b"private-fixture-query" not in body
