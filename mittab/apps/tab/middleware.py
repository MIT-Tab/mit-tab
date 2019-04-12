import re

from django.contrib.auth.views import login
from django.http import HttpResponseRedirect
from django.core.cache import cache

string_white_list = ('/accounts/login/', '/static/css/stylesheet.css',
        '/static/images/title_banner.png', '/pairings/pairinglist/', '/stat',
        '/e_ballots/', '/pairings/missing_ballots/')

regex_white_list = (
    re.compile("/e_ballots/\S+"),
    re.compile("/public_status/(\d+)"),
)


class Login:
    "This middleware requires a login for every view"
    def process_request(self, request):

        whitelisted = request.path in string_white_list or \
                      any(elem.match(request.path) for elem in regex_white_list)

        if not whitelisted and request.user.is_anonymous():
            if request.POST:
                return login(request)
            else:
                return HttpResponseRedirect('/accounts/login/?next=%s' % request.path)
