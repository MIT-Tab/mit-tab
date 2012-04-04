from django.contrib.auth.views import login
from django.http import HttpResponseRedirect
from django.core.cache import cache

login_white_list = ('/accounts/login/', '/static/css/stylesheet.css', '/static/images/title_banner.png')
#backend_white_list = ('/static/css/stylesheet.css', '/enter_tab/', '/static/images/title_banner.png')

class Login:
    "This middleware requires a login for every view"
    def process_request(self, request):
        if request.path not in login_white_list and request.user.is_anonymous():
            if request.POST:
                return login(request)
            else:
                return HttpResponseRedirect('/accounts/login/?next=%s' % request.path)