import re

from django.contrib.auth.views import login
from django.core.cache import cache

from mittab.apps.tab.helpers import redirect_and_flash_info


white_list = (
    re.compile('^/accounts/login/$'),
    re.compile('^/pairings/pairinglist/$'),
    re.compile('^/e_ballots/$'),
    re.compile("/e_ballots/\S+"),
    re.compile('^/pairings/missing_ballots/$'),
    re.compile("/public_status/(\d+)"),
    re.compile("/(404|403|500)/$"),
)

class Login:
    """This middleware requires a login for every view"""
    def process_request(self, request):

        whitelisted = any(regex.match(request.path) for regex in white_list)

        if not whitelisted and request.user.is_anonymous():
            if request.POST:
                return login(request)
            else:
                return redirect_and_flash_info(request,
                        'You must be logged in to view that page',
                        path='/accounts/login/?next=%s' % request.path)
