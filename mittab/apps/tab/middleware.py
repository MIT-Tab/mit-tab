import re

from django.contrib.auth.views import login
from django.http import HttpResponseRedirect
from django.core.cache import cache
from models import TabSettings

login_white_list = ('/accounts/login/', '/static/css/stylesheet.css',
        '/static/images/title_banner.png', '/pairings/pairinglist/', '/stat')

e_ballot_regex = re.compile("/e_ballots/\S+")
#backend_white_list = ('/static/css/stylesheet.css', '/enter_tab/', '/static/images/title_banner.png')

class Login:
    "This middleware requires a login for every view"
    def process_request(self, request):
        whitelisted = (request.path in login_white_list)
        whitelisted = whitelisted or self._is_eballot_url(request.path)
        if not whitelisted and request.user.is_anonymous():
            if request.POST:
                return login(request)
            else:
                return HttpResponseRedirect('/accounts/login/?next=%s' % request.path)

    def _is_eballot_url(self, path):
        if not TabSettings.get('allow_eballots', 0) == 1:
            return False
        return (path == '/e_ballots/') or e_ballot_regex.match(path)

