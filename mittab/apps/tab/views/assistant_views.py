"""Staff entry point for the optional tournament assistant."""

from django.conf import settings
from django.http import HttpResponseForbidden, HttpResponseNotFound, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from mittab.apps.tab import assistant
from mittab.apps.tab.assistant_sources import search_source, source_url
from mittab.apps.tab.auth_roles import is_apda_board_user


def _source_links(sources):
    unique = {(item["path"], item["line"]): item for item in sources}
    return [
        {**item, "url": source_url(path, line)}
        for (path, line), item in list(unique.items())[:8]
    ]


@sensitive_post_parameters("question", "history")
@never_cache
@require_http_methods(["GET", "POST"])
def ask_mittab(request):
    if not settings.MITTAB_ASSISTANT_ENABLED:
        return HttpResponseNotFound()
    if not request.user.is_superuser or is_apda_board_user(request.user):
        return HttpResponseForbidden()
    if request.method == "GET":
        return render(request, "tab/assistant.html", {"can_ask": assistant.is_configured()})

    question = request.POST.get("question", "").strip()
    history = request.POST.get("history", "")
    if not 2 <= len(question) <= 600 or len(history) > 2400:
        return JsonResponse({"message": "Enter a question between 2 and 600 characters."}, status=400)

    if request.POST.get("mode") == "ask":
        try:
            content, sources = assistant.answer(question, history)
            return JsonResponse({"answer": content, "sources": _source_links(sources)})
        except assistant.AssistantUnavailable:
            message = "Chat is unavailable. You can still search MIT-Tab’s code and docs."
    else:
        message = ""
    sources = _source_links(search_source(question))
    return JsonResponse({"message": message, "sources": sources})
