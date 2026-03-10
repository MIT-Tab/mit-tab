from django.contrib.auth.backends import ModelBackend

from mittab.apps.tab.auth_roles import is_apda_board_access_open, is_apda_board_user


class TabAuthenticationBackend(ModelBackend):
    """Enforce APDA board access timing while preserving default auth behavior."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        user = super().authenticate(
            request,
            username=username,
            password=password,
            **kwargs,
        )
        if user and is_apda_board_user(user) and not is_apda_board_access_open():
            return None
        return user
