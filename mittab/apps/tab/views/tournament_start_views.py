from django import forms
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, reverse
from django.utils import timezone

from mittab.apps.registration.models import InfoLink, InfoText
from mittab.apps.tab.auth_roles import STAFF_PRESET_DETAILS
from mittab.apps.tab.forms import (
    CustomTournamentStaffFormSetFactory,
    StartTournamentWizardForm,
)
from mittab.apps.tab.helpers import redirect_and_flash_success
from mittab.apps.tab.models import (
    DEFAULT_TOURNAMENT_NAME,
    TabSettings,
    TournamentStaffRole,
)
from mittab.apps.tab.staff_invites import (
    get_or_create_staff_invite_user,
    send_staff_invite_email,
)
from mittab.apps.tab.tournament_start import (
    START_TOURNAMENT_STAFF_INVITES_SENT,
    START_TOURNAMENT_WIZARD_COMPLETED,
    is_start_tournament_wizard_complete,
)
from mittab.apps.tab.tournament_staff import CORE_TOURNAMENT_STAFF_FIELDS
from mittab.apps.tab.views.staff_invite_views import StaffInviteForm
from mittab.libs.cacheing.public_cache import invalidate_all_public_caches
from mittab.libs.email_service import EmailServiceError


FACEBOOK_LINK_TITLE = "Facebook"
INVITE_PREFIX = "invite"
CUSTOM_STAFF_PREFIX = "custom_staff"
SEND_INVITE_ACTION = "send_invite"
STAFF_SETTING_FIELDS = tuple(
    setting for setting, _label in CORE_TOURNAMENT_STAFF_FIELDS
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
        "host_school": TabSettings.get("host_school", ""),
        "host_school_name": TabSettings.get("host_school_name", ""),
        "tournament_type": TabSettings.get("tournament_type", 0),
        "sanctioned": bool(TabSettings.get("sanctioned_apda_tournament", 1)),
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
            invite_count = int(
                TabSettings.get(START_TOURNAMENT_STAFF_INVITES_SENT, 0) or 0
            ) + 1
            TabSettings.set(START_TOURNAMENT_STAFF_INVITES_SENT, invite_count)
    except (EmailServiceError, ImproperlyConfigured) as exc:
        invite_form.add_error(None, f"Could not send the staff invite: {exc}")
        return None
    return invite_count


def _custom_staff_initial():
    return [
        {"title": role.title, "names": role.names}
        for role in TournamentStaffRole.objects.all()
    ]


def _save_wizard(form, custom_staff_formset):
    cleaned = form.cleaned_data
    with transaction.atomic():
        TabSettings.set("tournament_name", cleaned["tournament_name"].strip())
        TabSettings.set("host_school", cleaned["host_school"])
        TabSettings.set(
            "host_school_name",
            dict(form.fields["host_school"].choices).get(
                cleaned["host_school"], cleaned["host_school"]
            ),
        )
        TabSettings.set("tournament_type", cleaned["tournament_type"])
        TabSettings.set(
            "sanctioned_apda_tournament", int(cleaned["sanctioned"])
        )
        if not TabSettings.get("tournament_date", None):
            TabSettings.set("tournament_date", timezone.localdate().isoformat())
        TabSettings.set("open_speaks", cleaned["open_speaks"])
        TabSettings.set("open_ranks", cleaned["open_ranks"])
        for field in STAFF_SETTING_FIELDS:
            TabSettings.set(field, cleaned[field].strip())

        TournamentStaffRole.objects.all().delete()
        custom_roles = []
        for sort_order, role in enumerate(custom_staff_formset.cleaned_data):
            if role.get("DELETE") or not role.get("title"):
                continue
            custom_roles.append(TournamentStaffRole(
                title=role["title"],
                names=role["names"],
                sort_order=sort_order,
            ))
        TournamentStaffRole.objects.bulk_create(custom_roles)

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
    invite_count = int(
        TabSettings.get(START_TOURNAMENT_STAFF_INVITES_SENT, 0) or 0
    )
    # The staff invite is only mandatory during first-time setup; afterwards
    # staff are managed from the dedicated Invite Staff page.
    invite_form = None
    action = request.POST.get("action") if request.method == "POST" else None

    if request.method == "POST" and action == SEND_INVITE_ACTION:
        if was_complete:
            return JsonResponse(
                {"ok": False, "message": "Tournament setup is already complete."},
                status=400,
            )
        invite_form = WizardStaffInviteForm(
            request.POST,
            prefix=INVITE_PREFIX,
            inviter=request.user,
        )
        if invite_form.is_valid():
            invite_count = _send_invite(request, invite_form)
            if invite_count is not None:
                message = (
                    f"Invite sent to {invite_form.cleaned_data['email']}. "
                    "You can send another invite or continue setup."
                )
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({
                        "ok": True,
                        "message": message,
                        "invite_count": invite_count,
                    })
                return redirect_and_flash_success(
                    request,
                    message,
                    path=reverse("start_tournament"),
                )
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"ok": False, "errors": invite_form.errors.get_json_data()},
                status=400,
            )
        form = StartTournamentWizardForm(
            initial=_initial_values(is_initial_setup=True),
        )
        custom_staff_formset = CustomTournamentStaffFormSetFactory(
            initial=_custom_staff_initial(),
            prefix=CUSTOM_STAFF_PREFIX,
        )
    elif request.method == "POST":
        form = StartTournamentWizardForm(request.POST)
        custom_staff_formset = CustomTournamentStaffFormSetFactory(
            request.POST,
            prefix=CUSTOM_STAFF_PREFIX,
        )
        basics_valid = form.is_valid()
        custom_staff_valid = custom_staff_formset.is_valid()
        invite_requirement_met = was_complete or invite_count > 0
        if not invite_requirement_met:
            form.add_error(
                None,
                "Send at least one tab staff invite before continuing.",
            )
        if basics_valid and custom_staff_valid and invite_requirement_met:
            _save_wizard(form, custom_staff_formset)
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
        if not was_complete:
            invite_form = WizardStaffInviteForm(
                prefix=INVITE_PREFIX,
                inviter=request.user,
            )
    else:
        form = StartTournamentWizardForm(
            initial=_initial_values(is_initial_setup=not was_complete),
        )
        custom_staff_formset = CustomTournamentStaffFormSetFactory(
            initial=_custom_staff_initial(),
            prefix=CUSTOM_STAFF_PREFIX,
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
            "custom_staff_formset": custom_staff_formset,
            "invite_form": invite_form,
            "invite_count": invite_count,
            "staff_preset_details": STAFF_PRESET_DETAILS,
            "is_initial_setup": not was_complete,
            "title": "Tournament Basics",
        },
    )
