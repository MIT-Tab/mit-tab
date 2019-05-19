import re

from django.contrib.auth.views import login

from mittab.apps.tab.helpers import redirect_and_flash_info

LOGIN_WHITELIST = ("/accounts/login/", "/pairings/pairinglist/",
                   "/pairings/missing_ballots/", "/e_ballots/", "/404/",
                   "/403/", "/500/")

EBALLOT_REGEX = re.compile(r"/e_ballots/\S+")


class Login:
    """This middleware requires a login for every view"""

    def process_request(self, request):
        whitelisted = (request.path in LOGIN_WHITELIST) or \
                EBALLOT_REGEX.match(request.path)

        if not whitelisted and request.user.is_anonymous():
            if request.POST:
                return login(request)
            else:
                return redirect_and_flash_info(
                    request,
                    "You must be logged in to view that page",
                    path="/accounts/login/?next=%s" % request.path)
