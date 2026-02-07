from django.contrib import messages
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


def get_redirect_target(request, path=None, fallback="/"):
    """
    When we hit save on a form, this method helps us determine
    where to redirect the user afterwards.
    Preference order:
        1) Explicit path argument
        2) Hidden form field (POST) named 'return_to'
        3) Explicit GET parameters (?return_to= or ?next=)
        4) Session-stored return target
        5) HTTP referer header
        6) Provided fallback (defaults to '/')
    """
    candidates = [
        path,
        request.POST.get("return_to"),
        request.GET.get("return_to"),
        request.GET.get("next"),
        request.session.get("_return_to"),
        request.META.get("HTTP_REFERER"),
    ]

    allowed_hosts = {request.get_host()}
    require_https = request.is_secure()

    for candidate in candidates:
        if candidate and url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts=allowed_hosts,
            require_https=require_https,
        ):
            return candidate

    if fallback and url_has_allowed_host_and_scheme(
        fallback,
        allowed_hosts=allowed_hosts,
        require_https=require_https,
    ):
        return fallback

    if fallback is None:
        return None

    return "/"


def redirect_and_flash_info(request, message, **kwargs):
    return redirect_and_flash(request, message, messages.INFO, **kwargs)


def redirect_and_flash_success(request, message, **kwargs):
    return redirect_and_flash(request, message, messages.SUCCESS, **kwargs)


def redirect_and_flash_error(request, message, **kwargs):
    return redirect_and_flash(request, message, messages.ERROR, **kwargs)


def redirect_and_flash(request, message, message_level, **kwargs):
    path = get_redirect_target(request, kwargs.get("path"))
    messages.add_message(request, message_level, message)
    return redirect(path)
