from functools import wraps
from secrets import token_urlsafe
from urllib.parse import unquote, urlsplit

from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache

from mittab.apps.tab.models import Judge, Team


PORTAL_LIFETIME_SECONDS = 48 * 60 * 60


def is_code_link(url):
    try:
        path = unquote(urlsplit(url).path)
    except ValueError:
        return False
    if not path.startswith(("/public/e-ballots/", "/team_portal/")):
        return False
    try:
        return resolve(path.rstrip("/") + "/").view_name.startswith("legacy_")
    except Resolver404:
        return False


class PortalSession:
    def __init__(self, model, code_field, sign_in_view, label):
        self.model = model
        self.code_field = code_field
        self.sign_in_view = sign_in_view
        self.label = label
        self.session_key = f"{label}_portal_session"

    def _fingerprint(self, identity):
        return salted_hmac(
            self.session_key, getattr(identity, self.code_field), algorithm="sha256"
        ).hexdigest()

    def _identity(self, request, queryset):
        entry = request.session.get(self.session_key)
        if not entry:
            return None
        if entry["expires_at"] > timezone.now().timestamp():
            identity = queryset.filter(pk=entry["id"]).first()
            if identity and constant_time_compare(entry["code_hash"], self._fingerprint(identity)):
                return identity
        request.session.pop(self.session_key, None)
        return None

    def sign_in(self, request, code, destination, **kwargs):
        identity = (
            self.model.objects.filter(**{f"{self.code_field}__iexact": code}).first()
            if code else None
        )
        if identity:
            request.session.cycle_key()
            request.session[self.session_key] = {
                "id": identity.pk,
                "code_hash": self._fingerprint(identity),
                "expires_at": timezone.now().timestamp() + PORTAL_LIFETIME_SECONDS,
                "token": token_urlsafe(),
            }
            return redirect(destination, **kwargs)
        message = (
            f"We couldn't find a {self.label} for that code. Double-check and try again."
            if code else f"Please enter the {self.label} code provided by tab."
        )
        messages.error(request, message)
        return redirect(self.sign_in_view)

    def sign_in_from_link(self, request, code, destination, **kwargs):
        response = self.sign_in(request, code, destination, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
        return response

    def resume(self, request):
        return self._identity(request, self.model.objects.all())

    def sign_out(self, request):
        request.session.pop(self.session_key, None)
        request.session.cycle_key()
        return redirect(self.sign_in_view)

    def require_identity(self, view=None, *, queryset=None):
        def decorate(handler):
            @never_cache
            @wraps(handler)
            def wrapped(request, *args, **kwargs):
                identities = self.model.objects.all() if queryset is None else queryset
                identity = self._identity(request, identities)
                if identity is None:
                    return redirect(self.sign_in_view)

                token = request.session[self.session_key]["token"]
                if request.method == "POST" and not constant_time_compare(
                    request.POST.get("portal_token", ""), token
                ):
                    return HttpResponseForbidden(
                        "Your sign-in has changed. Reopen the portal and try again."
                    )
                request.portal_token = token
                return handler(request, identity, *args, **kwargs)

            return wrapped

        return decorate(view) if view else decorate


judge_session = PortalSession(Judge, "ballot_code", "e_ballot_search", "judge")
team_session = PortalSession(Team, "team_code", "team_portal_search", "team")
