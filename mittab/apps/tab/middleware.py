import re

from django.contrib.auth.views import LoginView
from django.http import HttpResponse, JsonResponse

from mittab.apps.tab.helpers import redirect_and_flash_info
from mittab.apps.tab.models import Outround, TabSettings
from mittab.libs.backup import is_backup_active

LOGIN_WHITELIST = ("/accounts/login/", "/pairings/pairinglist/",
                   "/pairings/missing_ballots/", "/e_ballots/", "/404/",
                   "/403/", "/500/", "/teams/", "/judges/",
                   "/outround_pairings/pairinglist/0/",
                   "/outround_pairings/pairinglist/1/",
                   "/json", "/api/varsity-speaker-awards",
                   "/api/novice-speaker-awards", "/api/varsity-team-placements",
                   "/api/novice-team-placements", "/api/non-placing-teams",
                   "/api/new-debater-data", "/api/new-schools")

EBALLOT_REGEX = re.compile(r"/e_ballots/\S+")


class Login:
    """This middleware requires a login for every view"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        whitelisted = (
            request.path in LOGIN_WHITELIST or
            EBALLOT_REGEX.match(request.path) or
            request.path.startswith("/registration")
        )

        if not whitelisted and request.user.is_anonymous:
            if request.POST:
                view = LoginView.as_view(template_name="registration/login.html")
                return view(request)
            else:
                return redirect_and_flash_info(
                    request,
                    "You must be logged in to view that page",
                    path="/accounts/login/?next=%s" % request.path)
        else:
            return self.get_response(request)


class TournamentStatusCheck:
    """Middleware to check tournament status for API endpoints."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith("/api/"):
            return self.get_response(request)

        finals = Outround.objects.filter(num_teams=2)
        if not finals.exists() or finals.filter(victor=Outround.UNKNOWN).exists():
            return JsonResponse({"error": "Tournament incomplete"}, status=409)

        if not TabSettings.get("results_published", False):
            return JsonResponse({"error": "Results not published"}, status=423)

        return self.get_response(request)


class FailoverDuringBackup:
    """
    Redirect traffic during a backup to a page which won't do any database
    reads/writes
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_backup_active():
            return HttpResponse(
                """
                A backup is in process. Try again in a few seconds.
                If you were submitting a form, you will need to re-submit it.
                """
            )
        return self.get_response(request)
