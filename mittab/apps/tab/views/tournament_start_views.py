from django import forms
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.shortcuts import render, reverse

from mittab.apps.registration.models import InfoLink, InfoText
from mittab.apps.tab.auth_roles import STAFF_PRESET_DETAILS
from mittab.apps.tab.forms import StartTournamentWizardForm
from mittab.apps.tab.helpers import redirect_and_flash_success
from mittab.apps.tab.models import DEFAULT_TOURNAMENT_NAME, TabSettings
from mittab.apps.tab.staff_invites import (
    get_or_create_staff_invite_user,
    send_staff_invite_email,
)
from mittab.apps.tab.tournament_start import (
    START_TOURNAMENT_WIZARD_COMPLETED,
    is_start_tournament_wizard_complete,
)
from mittab.apps.tab.views.staff_invite_views import StaffInviteForm
from mittab.libs.cacheing.public_cache import invalidate_all_public_caches
from mittab.libs.email_service import EmailServiceError


FACEBOOK_LINK_TITLE = "Facebook"
INVITE_PREFIX = "invite"
STAFF_SETTING_FIELDS = (
    "tournament_director",
    "tab_director",
    "equity_officers",
    "tournament_observer",
)


class WizardStaffInviteForm(StaffInviteForm):
    """Staff invite that must go to someone other than the person signing up."""

    def __init__(self, *args, inviter=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.inviter = inviter
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "custom-select" if isinstance(field.widget, forms.Select)
                else "form-control"
            )
        self.fields["email"].widget.attrs["placeholder"] = "colleague@example.com"
        self.fields["username"].widget.attrs["placeholder"] = "e.g. jsmith"

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                self.fields[name].widget.attrs["class"] += " is-invalid"

    def clean(self):
        cleaned_data = super().clean()
        if self.inviter is None:
            return cleaned_data
        email = (cleaned_data.get("email") or "").lower()
        username = cleaned_data.get("username") or ""
        inviter_email = (self.inviter.email or "").lower()
        if username == self.inviter.username or (
                inviter_email and email == inviter_email):
            raise forms.ValidationError(
                "Invite a different person. Every tab staff member needs "
                "their own login."
            )
        return cleaned_data


def _initial_values(is_initial_setup=False):
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
        "facebook_url": (
            facebook_link.url
            if facebook_link and not is_initial_setup
            else ""
        ),
        **{
            field: TabSettings.get(field, "")
            for field in STAFF_SETTING_FIELDS
        },
        "open_speaks": TabSettings.get("open_speaks", 0) or None,
        "open_ranks": TabSettings.get("open_ranks", 0) or None,
    }


def _send_invite(request, invite_form):
    """Create and email the invite without retaining a failed account change."""
    try:
        with transaction.atomic():
            user, _created = get_or_create_staff_invite_user(
                invite_form.cleaned_data["email"],
                invite_form.cleaned_data["username"],
                invite_form.cleaned_data["permission_preset"],
            )
            send_staff_invite_email(request, user)
    except (EmailServiceError, ImproperlyConfigured) as exc:
        invite_form.add_error(None, f"Could not send the staff invite: {exc}")
        return False
    return True


def _save_wizard(form):
    cleaned = form.cleaned_data
    with transaction.atomic():
        TabSettings.set("tournament_name", cleaned["tournament_name"].strip())
        TabSettings.set("open_speaks", cleaned["open_speaks"])
        TabSettings.set("open_ranks", cleaned["open_ranks"])
        for field in STAFF_SETTING_FIELDS:
            TabSettings.set(field, cleaned[field].strip())

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
    # The staff invite is only mandatory during first-time setup; afterwards
    # staff are managed from the dedicated Invite Staff page.
    invite_form = None

    if request.method == "POST":
        form = StartTournamentWizardForm(request.POST)
        if not was_complete:
            invite_form = WizardStaffInviteForm(
                request.POST,
                prefix=INVITE_PREFIX,
                inviter=request.user,
            )
        basics_valid = form.is_valid()
        invite_valid = invite_form is None or invite_form.is_valid()
        if basics_valid and invite_valid and (
                invite_form is None or _send_invite(request, invite_form)):
            _save_wizard(form)
            if was_complete:
                return redirect_and_flash_success(
                    request,
                    "Tournament basics updated.",
                    path=reverse("start_tournament"),
                )
            return redirect_and_flash_success(
                request,
                "Tournament basics saved and a staff invite was sent to "
                f"{invite_form.cleaned_data['email']}. Continue with the "
                "setup checklist.",
                path=reverse("tournament_todo"),
            )
    else:
        form = StartTournamentWizardForm(
            initial=_initial_values(is_initial_setup=not was_complete),
        )
        if not was_complete:
            invite_form = WizardStaffInviteForm(
                prefix=INVITE_PREFIX,
                inviter=request.user,
            )

    return render(
        request,
        "tab/start_tournament.html",
        {
            "form": form,
            "invite_form": invite_form,
            "staff_preset_details": STAFF_PRESET_DETAILS,
            "is_initial_setup": not was_complete,
            "title": "Tournament Basics",
        },
    )
