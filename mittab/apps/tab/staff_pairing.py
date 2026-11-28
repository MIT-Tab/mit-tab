"""Coordinate staff elimination writes and reject outdated pairing cards."""
import json
from functools import wraps
from types import SimpleNamespace

from django.contrib.auth import get_user
from django.db import transaction
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect

from mittab.apps.tab.auth_roles import is_apda_board_user, is_restricted_staff_user
from mittab.apps.tab.board_audit import pairing_revision
from mittab.apps.tab.models import Outround, TabSettings


STALE_PAIRING = "This pairing changed. Reload it before saving."


def serialize_staff_pairing_post(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method != "POST":
            return view(request, *args, **kwargs)
        with transaction.atomic():
            # Share the tournament lock across staff and Board pairing writes.
            lock_id = (TabSettings.objects.select_for_update().filter(key="cur_round")
                       .order_by("id").values_list("id", flat=True).first())
            if lock_id is None:
                return HttpResponse("Unable to complete this request.", status=503,
                                    content_type="text/plain; charset=utf-8",
                                    headers={"Cache-Control": "private, no-store"})
            session = type(request.session)(session_key=request.session.session_key)
            actor = get_user(SimpleNamespace(session=session))
            if (not actor.is_authenticated or actor.pk != request.user.pk
                    or is_apda_board_user(actor) or is_restricted_staff_user(actor)
                    or not actor.has_perm("tab.tab_settings.can_change")):
                return redirect("/403/")
            request.user = actor
            return view(request, *args, **kwargs)
    return wrapped


def stale_round_response(request, round_number):
    requested_round = request.POST.get("round")
    if requested_round is not None and requested_round != str(round_number):
        return HttpResponse("The current round changed. Reload this page and try again.",
                            status=409, content_type="text/plain; charset=utf-8",
                            headers={"Cache-Control": "private, no-store"})
    return None


def correction_error(message):
    return JsonResponse({"success": False, "error": message}, status=422,
                        headers={"Cache-Control": "private, no-store"})


def staff_elimination_correction(*, shared=False, choice=False):
    """Use the same revision in the card, its successful response, and the audit."""
    def decorate(view):
        @serialize_staff_pairing_post
        def correct(request, *args, **kwargs):
            round_id = args[0] if args else kwargs.get("round_id", kwargs.get("outround_id"))
            revision = request.POST.get("revision")
            if revision:
                pairing = Outround.objects.select_for_update().filter(pk=round_id).first()
                if pairing is not None and revision != pairing_revision(pairing):
                    if choice:
                        return HttpResponse(STALE_PAIRING, status=422,
                                            content_type="text/plain; charset=utf-8",
                                            headers={"Cache-Control": "private, no-store"})
                    return correction_error(STALE_PAIRING)

            response = view(request, *args, **kwargs)
            if response.status_code == 200 and isinstance(response, JsonResponse):
                result = json.loads(response.content)
                if result["success"] and (choice or revision):
                    result["revision"] = pairing_revision(Outround.objects.get(pk=round_id))
                    return JsonResponse(result, headers={"Cache-Control": "private, no-store"})
            return response

        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if shared and not (kwargs.get("outround") or kwargs.get("is_outround")):
                return view(request, *args, **kwargs)
            if request.method != "POST":
                return HttpResponseNotAllowed(["POST"])
            return correct(request, *args, **kwargs)
        return wrapped
    return decorate
