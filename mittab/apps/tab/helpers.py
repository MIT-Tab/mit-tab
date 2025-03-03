from django.contrib import messages
from django.shortcuts import redirect


def redirect_and_flash_info(request, message, **kwargs):
    return redirect_and_flash(request, message, messages.INFO, **kwargs)


def redirect_and_flash_success(request, message, **kwargs):
    return redirect_and_flash(request, message, messages.SUCCESS, **kwargs)


def redirect_and_flash_error(request, message, **kwargs):
    return redirect_and_flash(request, message, messages.ERROR, **kwargs)


def redirect_and_flash(request, message, message_level, **kwargs):
    path = kwargs.get("path", request.headers.get("referer", "/"))
    messages.add_message(request, message_level, message)
    return redirect(path)
