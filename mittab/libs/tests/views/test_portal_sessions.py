from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from mittab.apps.tab.models import Judge, JudgeExpectedCheckIn, School, Team
from mittab.apps.tab.portal_sessions import judge_session, team_session


pytestmark = pytest.mark.django_db


@pytest.fixture(name="portal", params=["judge", "team"])
def portal_fixture(request):
    if request.param == "judge":
        judge = Judge.objects.create(name="Remembered Judge", rank=5)
        return judge_session, judge, "judge_portal"
    team = Team.objects.create(
        name="Remembered Team", school=School.objects.create(name="Portal U"),
        seed=Team.UNSEEDED,
    )
    return team_session, team, "team_portal"


def test_portal_remembers_sign_in_for_48_hours(settings, portal):
    session, identity, name = portal
    start = timezone.now()
    client = Client()
    shortcut = reverse(session.sign_in_view)
    home = reverse(name)
    code = getattr(identity, session.code_field)

    with patch("mittab.apps.tab.portal_sessions.timezone.now", return_value=start):
        response = client.post(shortcut, {session.code_field: f" {code.upper()} "})

    assert response.url == home
    cookie = response.cookies[settings.SESSION_COOKIE_NAME]
    assert int(cookie["max-age"]) >= 48 * 60 * 60
    assert cookie["httponly"]

    reopened = Client()
    reopened.cookies[settings.SESSION_COOKIE_NAME] = cookie.value
    with patch(
        "mittab.apps.tab.portal_sessions.timezone.now",
        return_value=start + timedelta(hours=48, seconds=-1),
    ):
        assert reopened.get(shortcut).url == home
        response = reopened.get(home)
        assert response.status_code == 200
        assert identity.name.encode() in response.content
        assert code.encode() not in response.content
        assert "private" in response["Cache-Control"]
        assert "no-store" in response["Cache-Control"]

    with patch(
        "mittab.apps.tab.portal_sessions.timezone.now",
        return_value=start + timedelta(hours=48),
    ):
        token = reopened.session[session.session_key]["token"]
        assert reopened.post(home, {"portal_token": token}).url == shortcut
        assert reopened.get(shortcut).status_code == 200


def test_legacy_links_sign_in_and_never_accept_writes(client, portal):
    session, identity, name = portal
    code = getattr(identity, session.code_field)
    home = reverse(name)
    legacy = reverse(f"legacy_{name}", args=[code])
    assert client.post(legacy, {session.code_field: code}).status_code == 405
    assert session.session_key not in client.session
    response = client.get(legacy)
    assert response.url == home
    assert response["Referrer-Policy"] == "no-referrer"
    assert "no-store" in response["Cache-Control"]
    for referrer_path in (legacy, legacy.rstrip("/")):
        response = client.get(home, HTTP_REFERER=f"http://testserver{referrer_path}")
        assert response.status_code == 200
        assert client.session["_return_to"] == home


@pytest.mark.parametrize("revoke_by", ["change_code", "delete"])
def test_revoked_code_session_redirects_to_sign_in(client, portal, revoke_by):
    session, identity, name = portal
    old_code = getattr(identity, session.code_field)
    client.post(reverse(session.sign_in_view), {session.code_field: old_code})
    if revoke_by == "change_code":
        session.model.objects.filter(pk=identity.pk).update(
            **{session.code_field: "updated-portal-code"}
        )
    else:
        identity.delete()

    assert client.get(reverse(name)).url == reverse(session.sign_in_view)
    assert session.session_key not in client.session


def test_sign_out_requires_csrf_and_preserves_other_portal_and_staff(settings):
    client = Client(enforce_csrf_checks=True)
    staff = get_user_model().objects.create_user(username="portal-staff", is_staff=True)
    client.force_login(staff)
    judge = Judge.objects.create(name="Remembered Judge", rank=5)
    team = Team.objects.create(
        name="Remembered Team", school=School.objects.create(name="Portal U"),
        seed=Team.UNSEEDED,
    )

    for name, code in (
        ("judge_portal", judge.ballot_code), ("team_portal", team.team_code)
    ):
        response = client.get(reverse(f"legacy_{name}", args=[code]), follow=True)
        assert response.status_code == 200

    sign_out = reverse("judge_portal_sign_out")
    token = client.session[judge_session.session_key]["token"]
    assert client.get(sign_out).status_code == 405
    assert client.post(sign_out, {"portal_token": token}).status_code == 403
    csrf = client.cookies[settings.CSRF_COOKIE_NAME].value
    response = client.post(
        sign_out, {"portal_token": token, "csrfmiddlewaretoken": csrf}
    )
    assert response.url == reverse("e_ballot_search")
    assert client.get(reverse("judge_portal")).url == reverse("e_ballot_search")
    assert client.get(reverse("team_portal")).status_code == 200
    assert client.session["_auth_user_id"] == str(staff.pk)


def test_open_judge_form_cannot_write_after_switching_judges(client):
    judge = Judge.objects.create(name="First Judge", rank=5)
    other = Judge.objects.create(name="Second Judge", rank=5)
    client.get(reverse("legacy_judge_portal", args=[judge.ballot_code]))
    old_token = client.session[judge_session.session_key]["token"]
    client.get(reverse("legacy_judge_portal", args=[other.ballot_code]))

    response = client.post(
        reverse("judge_portal"),
        {"portal_token": old_token, "availability_outround": "on"},
    )
    assert response.status_code == 403
    assert not JudgeExpectedCheckIn.objects.filter(judge__in=(judge, other)).exists()
    response = client.post(
        reverse("judge_portal_sign_out"), {"portal_token": old_token}
    )
    assert response.status_code == 403
    assert client.session[judge_session.session_key]["id"] == other.pk


def test_legacy_ballot_bookmark_redirects_to_same_ballot_without_code(client):
    judge = Judge.objects.create(name="Booked Judge", rank=5)
    url = reverse("legacy_view_submitted_ballot_round", args=[judge.ballot_code, 42])
    response = client.get(url, {
        "destination": "https://example.com/",
        "next": "https://example.com/",
        "return_to": "https://example.com/",
        "round_id": 99,
    })
    assert response.url == reverse("view_submitted_ballot_round", args=[42])
    assert client.session[judge_session.session_key]["id"] == judge.pk
