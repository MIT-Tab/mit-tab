import re

from django.contrib.auth.views import login
from django.core.cache import cache

from mittab.apps.tab.helpers import redirect_and_flash_info

login_white_list = ('/accounts/login/', '/pairings/pairinglist/',
                    '/pairings/missing_ballots/', '/e_ballots/', '/404/',
                    '/403/', '/500/')

e_ballot_regex = re.compile("/e_ballots/\S+")


class Login:
    """This middleware requires a login for every view"""

    def process_request(self, request):
        whitelisted = (request.path in login_white_list) or \
                e_ballot_regex.match(request.path)

        if not whitelisted and request.user.is_anonymous():
            if request.POST:
                return login(request)
            else:
                return redirect_and_flash_info(
                    request,
                    'You must be logged in to view that page',
                    path='/accounts/login/?next=%s' % request.path)
