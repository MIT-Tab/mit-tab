"""Public tab card publication and caching."""

from django.shortcuts import redirect, render
from django.utils.cache import add_never_cache_headers

from mittab.apps.tab.auth_roles import is_apda_board_user
from mittab.apps.tab.public_pages import tab_cards_are_public
from mittab.apps.tab.tab_cards import get_tab_cards
from mittab.libs.cacheing.public_cache import cache_public_view


def public_tab_cards(request):
    """Check publication before the cache, and keep staff on private routes."""
    if (request.user.has_perm("tab.tab_settings.can_change")
            or is_apda_board_user(request.user)):
        response = redirect("all_tab_cards")
    elif not tab_cards_are_public():
        response = redirect("public_access_error")
    elif request.user.is_authenticated:
        response = _render_public_tab_cards(request)
    else:
        return cached_public_tab_cards(request)
    add_never_cache_headers(response)
    return response


def _render_public_tab_cards(request):
    return render(request, "public/tab_cards.html", {
        "cards": get_tab_cards(), "public_view": True,
    })


@cache_public_view(timeout=60)
def cached_public_tab_cards(request):
    return _render_public_tab_cards(request)
