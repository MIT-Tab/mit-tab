from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.urls import reverse

from mittab.apps.tab.models import PublicHomeShortcut


class StaffLoginView(LoginView):
    template_name = "public/staff_login.html"

    def get_success_url(self):
        redirect_to = self.get_redirect_url()
        if not PublicHomeShortcut.is_homepage_setup_complete():
            messages.info(
                self.request,
                "Welcome to your new tournament. "
                "Please set up your homepage before continuing.",
            )
            return reverse("homepage_setup")
        if redirect_to:
            return redirect_to
        return settings.LOGIN_REDIRECT_URL
