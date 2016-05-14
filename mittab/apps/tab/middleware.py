import re

from django.contrib.auth.views import login
from django.http import HttpResponseRedirect
from django.core.cache import cache

login_white_list = ('/accounts/login/', '/static/css/stylesheet.css',
        '/static/images/title_banner.png', '/pairings/pairinglist/',
        '/e_ballots/', '/stat')
e_ballot_regex = re.compile("/e_ballots/\S+")
#backend_white_list = ('/static/css/stylesheet.css', '/enter_tab/', '/static/images/title_banner.png')

class Login:
    "This middleware requires a login for every view"
    def process_request(self, request):
        whitelisted = (request.path in login_white_list) or e_ballot_regex.match(request.path)
        if not whitelisted and request.user.is_anonymous():
            if request.POST:
                return login(request)
            else:
                return HttpResponseRedirect('/accounts/login/?next=%s' % request.path)
