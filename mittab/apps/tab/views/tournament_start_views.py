from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.shortcuts import render, reverse

from mittab.apps.registration.models import InfoLink, InfoText
from mittab.apps.tab.forms import StartTournamentWizardForm
from mittab.apps.tab.helpers import redirect_and_flash_success
from mittab.apps.tab.models import DEFAULT_TOURNAMENT_NAME, TabSettings
from mittab.apps.tab.tournament_start import (
    START_TOURNAMENT_WIZARD_COMPLETED,
    is_start_tournament_wizard_complete,
)
from mittab.libs.cacheing.public_cache import invalidate_all_public_caches


FACEBOOK_LINK_TITLE = "Facebook"


def _initial_values():
    ga_info = InfoText.objects.filter(title=InfoText.DEFAULT_TITLE).first()
    facebook_link = InfoLink.objects.filter(
        title__iexact=FACEBOOK_LINK_TITLE,
    ).first()
    tournament_name = TabSettings.get("tournament_name", DEFAULT_TOURNAMENT_NAME)
    ga_room = ga_info.text if ga_info else ""
    # Never prefill initialization placeholders: the wizard should collect real
    # answers instead of letting staff click through the defaults.
    if tournament_name == DEFAULT_TOURNAMENT_NAME:
        tournament_name = ""
    if ga_room == InfoText.DEFAULT_TEXT:
        ga_room = ""
    return {
        "tournament_name": tournament_name,
        "ga_room": ga_room,
        "facebook_url": facebook_link.url if facebook_link else "",
        "total_rounds": TabSettings.get("tot_rounds", 5),
        "varsity_break_size": TabSettings.get("var_teams_to_break", 8),
        "novice_break_size": TabSettings.get("nov_teams_to_break", 4),
        "open_speaks": TabSettings.get("open_speaks", 0) or None,
        "open_ranks": TabSettings.get("open_ranks", 0) or None,
    }


def _save_wizard(form):
    cleaned = form.cleaned_data
    with transaction.atomic():
        TabSettings.set("tournament_name", cleaned["tournament_name"].strip())
        TabSettings.set("tot_rounds", cleaned["total_rounds"])
        TabSettings.set("var_teams_to_break", cleaned["varsity_break_size"])
        TabSettings.set("nov_teams_to_break", cleaned["novice_break_size"])
        TabSettings.set("open_speaks", cleaned["open_speaks"])
        TabSettings.set("open_ranks", cleaned["open_ranks"])

        ga_info = InfoText.objects.filter(title=InfoText.DEFAULT_TITLE).first()
        if ga_info is None:
            ga_info = InfoText(title=InfoText.DEFAULT_TITLE)
        ga_info.text = cleaned["ga_room"].strip()
        ga_info.is_active = True
        ga_info.save()

        facebook_link = InfoLink.objects.filter(
            title__iexact=FACEBOOK_LINK_TITLE,
        ).first()
        if facebook_link is None:
            facebook_link = InfoLink(title=FACEBOOK_LINK_TITLE)
        facebook_link.url = cleaned["facebook_url"]
        facebook_link.description = "Tournament updates and announcements."
        facebook_link.is_active = True
        facebook_link.save()

        # Set completion last so a partially saved wizard can never unlock the app.
        TabSettings.set(START_TOURNAMENT_WIZARD_COMPLETED, 1)

    invalidate_all_public_caches()


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def start_tournament(request):
    was_complete = is_start_tournament_wizard_complete()

    if request.method == "POST":
        form = StartTournamentWizardForm(request.POST)
        if form.is_valid():
            _save_wizard(form)
            if was_complete:
                return redirect_and_flash_success(
                    request,
                    "Tournament basics updated.",
                    path=reverse("start_tournament"),
                )
            return redirect_and_flash_success(
                request,
                "Tournament basics saved. Continue with the setup checklist.",
                path=reverse("tournament_todo"),
            )
    else:
        form = StartTournamentWizardForm(initial=_initial_values())

    return render(
        request,
        "tab/start_tournament.html",
        {
            "form": form,
            "is_initial_setup": not was_complete,
            "title": "Tournament Basics",
        },
    )
