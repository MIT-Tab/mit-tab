import re

from django.contrib.auth.views import login
from django.http import HttpResponseRedirect
from django.core.cache import cache

string_white_list = ('/accounts/login/', '/static/css/stylesheet.css',
        '/static/images/title_banner.png', '/pairings/pairinglist/', '/stat',
        '/e_ballots/', '/pairings/missing_ballots/')

regex_white_list = (
    re.compile("/e_ballots/\S+"),
    re.compile("/public_status/team_name/([\w-]+)/"),
    re.compile("/public_status/debater_name/([\w-]+)/"),
)


class Login:
    "This middleware requires a login for every view"
    def process_request(self, request):

        whitelisted = any(request.path == elem for elem in string_white_list) or \
                      any(elem.fullmatch(request.path) for elem in regex_white_list)

        if not whitelisted and request.user.is_anonymous():
            if request.POST:
                return login(request)
            else:
                return HttpResponseRedirect('/accounts/login/?next=%s' % request.path)
