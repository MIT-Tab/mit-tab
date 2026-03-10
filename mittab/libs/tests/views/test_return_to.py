from django.http import HttpResponse
from django.test import RequestFactory

from mittab.apps.tab.helpers import get_redirect_target
from mittab.apps.tab.middleware import Login
from mittab.apps.tab.templatetags.tags import return_to_value, with_return_to


def test_get_redirect_target_prefers_session_value_over_referer():
    request = RequestFactory().get(
        "/pairings/status/?round=2",
        HTTP_HOST="testserver",
        HTTP_REFERER="/public/login/",
    )
    request.session = {"_return_to": "/pairings/status/?round=2"}

    assert get_redirect_target(request) == "/pairings/status/?round=2"


def test_get_redirect_target_still_honors_explicit_get_return_to():
    request = RequestFactory().get(
        "/pairings/status/?return_to=/public/teams/",
        HTTP_HOST="testserver",
    )

    assert get_redirect_target(request) == "/public/teams/"


def test_return_to_value_uses_current_request_url():
    request = RequestFactory().get(
        "/pairings/status/?round=4",
        HTTP_HOST="testserver",
    )
    request.session = {}

    assert return_to_value({"request": request}) == "/pairings/status/?round=4"


def test_return_to_value_prefers_session_target():
    request = RequestFactory().get(
        "/team/12/",
        HTTP_HOST="testserver",
    )
    request.session = {"_return_to": "/batch_checkin/"}

    assert return_to_value({"request": request}) == "/batch_checkin/"


def test_with_return_to_keeps_urls_clean():
    assert with_return_to("/public/teams/") == "/public/teams/"
    assert with_return_to("/public/teams/?foo=1") == "/public/teams/?foo=1"


def test_login_middleware_stores_return_to_for_get_requests():
    request = RequestFactory().get("/pairings/status/?round=1", HTTP_HOST="testserver")
    request.user = type("User", (), {"is_anonymous": False})()
    request.session = {}

    response = Login(lambda req: HttpResponse("ok"))(request)

    assert response.status_code == 200
    assert request.session["_return_to"] == "/pairings/status/?round=1"


def test_login_middleware_prefers_internal_referer_for_return_to():
    request = RequestFactory().get(
        "/team/12/",
        HTTP_HOST="testserver",
        HTTP_REFERER="http://testserver/batch_checkin/",
    )
    request.user = type("User", (), {"is_anonymous": False})()
    request.session = {}

    response = Login(lambda req: HttpResponse("ok"))(request)

    assert response.status_code == 200
    assert request.session["_return_to"] == "/batch_checkin/"
