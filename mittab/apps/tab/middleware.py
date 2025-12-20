import re

from django.contrib.auth.views import LoginView
from django.http import HttpResponse, JsonResponse

from mittab.apps.tab.helpers import redirect_and_flash_info
from mittab.apps.tab.public_rankings import get_standings_publication_setting
from mittab.libs.backup import is_backup_active

LOGIN_WHITELIST = ("/", "/public/", "/public/login/", "/public/pairings/",
                   "/public/missing-ballots/","/public/e-ballots/",
                   "/public/access-error/", "/404/", "/403/", "/500/",
                   "/public/teams/",
                   "/public/judges/",
                   "/public/team-rankings/",
                   "/public/speaker-rankings/",
                   "/public/ballots/",
                   "/public/outrounds/0/", "/public/outrounds/1/",
                   "/json", "/api/varsity-speaker-awards",
                   "/api/novice-speaker-awards", "/api/varsity-team-placements",
                   "/api/novice-team-placements", "/api/non-placing-teams",
                   "/api/new-debater-data", "/api/new-schools",
                   "/api/debater-counts", "/favicon.ico")

EBALLOT_REGEX = re.compile(r"/public/e-ballots/\S+")
API_PATH_REQUIREMENTS = {
    "/api/varsity-speaker-awards": "speaker",
    "/api/novice-speaker-awards": "speaker",
    "/api/varsity-team-placements": "team",
    "/api/novice-team-placements": "team",
    "/api/non-placing-teams": "team",
    "/api/new-debater-data": "shared",
    "/api/new-schools": "shared",
    "/api/debater-counts": "shared",
}
API_ERROR_MESSAGES = {
    "speaker": "Speaker results not published",
    "team": "Team results not published",
    "shared": "Standings data not published",
}


class Login:
    """This middleware requires a login for every view"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        whitelisted = (
            path in LOGIN_WHITELIST
            or path.startswith("/public/")
            or path.startswith("/registration/")
            or EBALLOT_REGEX.match(path)
        )

        if not whitelisted and request.user.is_anonymous:
            if request.POST:
                view = LoginView.as_view(template_name="public/staff_login.html")
                return view(request)
            else:
                return redirect_and_flash_info(
                    request,
                    "You must be logged in to view that page",
                    path=f"/public/login/?next={request.path}")
        else:
            return self.get_response(request)


class TournamentStatusCheck:
    """Middleware to check tournament status for API endpoints."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not path.startswith("/api/"):
            return self.get_response(request)

        requirement = API_PATH_REQUIREMENTS.get(path)
        if not requirement:
            return self.get_response(request)

        speaker_published = bool(
            get_standings_publication_setting("speaker_results")["published"]
        )
        team_published = bool(
            get_standings_publication_setting("team_results")["published"]
        )
        published_state = {
            "speaker": speaker_published,
            "team": team_published,
            "shared": speaker_published or team_published,
        }

        if not published_state[requirement]:
            return JsonResponse(
                {"error": API_ERROR_MESSAGES[requirement]},
                status=423,
            )

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
