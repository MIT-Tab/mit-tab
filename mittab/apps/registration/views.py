import json
import logging
from typing import Type, cast

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.forms import BaseFormSet, formset_factory
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
import requests
from requests.exceptions import RequestException

from mittab.apps.registration.forms import (
    InfoLinkForm,
    JudgeForm,
    RegistrationForm,
    RegistrationLinkForm,
    RegistrationSettingsForm,
    TeamNameChangeForm,
    TeamPortalScratchForm,
    TeamForm,
)
from mittab.apps.registration.emails import (
    send_registration_confirmation_email,
    send_registration_judge_code_emails,
    send_registration_team_portal_emails,
)
from mittab.apps.registration.services import (
    build_school_choices,
    fetch_private_debater_emails,
    get_round_config,
    log_registration_deleted,
    mask_private_email,
    registration_judge_initial,
    registration_team_initial,
    save_registration,
    school_value,
)
from mittab.apps.tab.models import Judge, Round, Scratch, TabSettings, Team
from mittab.apps.tab.helpers import (
    redirect_and_flash_error,
    redirect_and_flash_success,
)
from mittab.libs.cacheing.public_cache import invalidate_all_public_caches
from mittab.libs.email_service import EmailServiceError
from .models import (
    InfoLink,
    Registration,
    RegistrationChangeLog,
    RegistrationConfig,
    RegistrationLink,
)

LINK_KINDS = {
    "info": {
        "model": InfoLink,
        "form": InfoLinkForm,
        "label": "info",
        "label_plural": "info links",
        "redirect_url_name": "public_homepage",
    },
    "registration": {
        "model": RegistrationLink,
        "form": RegistrationLinkForm,
        "label": "registration",
        "label_plural": "registration links",
        "redirect_url_name": "registration_admin",
    },
}


def _link_kind_or_404(link_kind):
    config = LINK_KINDS.get(link_kind)
    if not config:
        raise Http404()
    return config

MAX_TEAMS = 200
logger = logging.getLogger(__name__)


class RegistrationTeamFormSet(BaseFormSet):
    def __init__(self, *args, school_choices=None, **kwargs):
        self.school_choices = school_choices or []
        super().__init__(*args, **kwargs)

    def _construct_form(self, i, **kwargs):
        kwargs["school_choices"] = self.school_choices
        return super()._construct_form(i, **kwargs)

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        free_seed_count = sum(
            1
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("seed_choice") == Team.FREE_SEED
        )
        if free_seed_count > 1:
            raise forms.ValidationError("Select at most one free seed")


TeamFormSet = cast(
    Type[RegistrationTeamFormSet],
    formset_factory(
        TeamForm,
        formset=RegistrationTeamFormSet,
        extra=0,
        can_delete=True,
        max_num=MAX_TEAMS,
    ),
)


class RegistrationJudgeFormSet(BaseFormSet):
    def __init__(self, *args, round_config=None, school_choices=None, **kwargs):
        self.round_config = round_config or {"prelims": [], "outround": None}
        self.school_choices = school_choices or []
        super().__init__(*args, **kwargs)

    def _construct_form(self, i, **kwargs):
        kwargs["round_config"] = self.round_config
        kwargs["school_choices"] = self.school_choices
        return super()._construct_form(i, **kwargs)

    @property
    def empty_form(self):
        form_class = getattr(self, "form")
        form = form_class(
            auto_id=self.auto_id,
            prefix=self.add_prefix("__prefix__"),
            empty_permitted=True,
            use_required_attribute=False,
            round_config=self.round_config,
            school_choices=self.school_choices,
        )
        self.add_fields(form, None)
        return form


JudgeFormSet = cast(
    Type[RegistrationJudgeFormSet],
    formset_factory(
        JudgeForm,
        formset=RegistrationJudgeFormSet,
        extra=0,
        can_delete=True,
    ),
)


def get_registration_forms(request, registration, school_choices, round_config):
    if request.method == "POST":
        reg_form = RegistrationForm(request.POST, school_choices=school_choices)
        team_formset = TeamFormSet(  # pylint: disable=unexpected-keyword-arg
            request.POST,
            prefix="teams",
            school_choices=school_choices,
        )
        judge_formset = JudgeFormSet(  # pylint: disable=unexpected-keyword-arg
            request.POST,
            prefix="judges",
            round_config=round_config,
            school_choices=school_choices,
        )
        return reg_form, team_formset, judge_formset
    if registration:
        reg_form = RegistrationForm(
            initial={
                "school": school_value(registration.school),
                "school_name": registration.school.name,
                "email": registration.email,
            },
            school_choices=school_choices,
        )
        teams_initial = [
            registration_team_initial(team)
            for team in registration.teams.select_related(
                "school", "hybrid_school"
            ).prefetch_related("debaters")
        ]
        judges_initial = [
            registration_judge_initial(judge, round_config)
            for judge in registration.judges.prefetch_related(
                "expected_checkins",
                "schools",
            )
        ]
    else:
        reg_form = RegistrationForm(school_choices=school_choices)
        teams_initial = []
        judges_initial = []
    team_formset = TeamFormSet(  # pylint: disable=unexpected-keyword-arg
        initial=teams_initial,
        prefix="teams",
        school_choices=school_choices,
    )
    judge_formset = JudgeFormSet(  # pylint: disable=unexpected-keyword-arg
        initial=judges_initial,
        prefix="judges",
        round_config=round_config,
        school_choices=school_choices,
    )
    return reg_form, team_formset, judge_formset


@require_http_methods(["GET", "POST"])
def registration_code_lookup(request):
    config = RegistrationConfig.get_or_create_active()
    can_modify = config.can_modify()

    if request.method == "POST":
        code = (request.POST.get("registration_code") or "").strip()

        if not can_modify:
            return redirect_and_flash_error(
                request,
                "Registration editing is currently disabled.",
                path=reverse("registration_code_lookup"),
            )

        if not code:
            return redirect_and_flash_error(
                request,
                "Please enter the registration code from your confirmation email.",
                path=reverse("registration_code_lookup"),
            )

        exists = Registration.objects.filter(herokunator_code=code).exists()
        if not exists:
            return redirect_and_flash_error(
                request,
                (
                    "We couldn't find a registration for that code. "
                    "Double-check and try again."
                ),
                path=reverse("registration_code_lookup"),
            )

        return redirect("registration_portal_edit", code=code)

    return render(
        request,
        "registration/code_lookup.html",
        {"can_modify_registration": can_modify},
    )


@require_http_methods(["GET", "POST"])
def registration_portal(request, code=None):
    registration = None
    if code:
        registration = (
            Registration.objects.select_related("school")
            .filter(herokunator_code=code)
            .first()
        )
        if not registration:
            raise Http404()
    school_choices = build_school_choices()
    round_config = get_round_config()
    reg_form, team_formset, judge_formset = get_registration_forms(
        request, registration, school_choices, round_config
    )
    config = RegistrationConfig.get_or_create_active()
    is_edit_mode = registration is not None
    can_create = config.can_create()
    can_modify = config.can_modify()
    can_submit = can_modify if is_edit_mode else can_create
    lock_message = None
    if not can_submit:
        lock_message = (
            "Registration updates are currently disabled."
            if is_edit_mode
            else "New registrations are currently closed."
        )
    if request.method == "POST":
        if reg_form.is_valid() and team_formset.is_valid() and judge_formset.is_valid():
            has_team = any(
                form.cleaned_data and not form.cleaned_data.get("DELETE")
                for form in team_formset.forms
            )
            has_judge = any(
                form.cleaned_data and not form.cleaned_data.get("DELETE")
                for form in judge_formset.forms
            )
            if not (has_team or has_judge):
                message = "Add at least one team or one judge"
                # pylint: disable=protected-access
                team_formset._non_form_errors = team_formset.error_class([message])
                judge_formset._non_form_errors = judge_formset.error_class([message])
            elif not can_submit:
                reg_form.add_error(
                    None, lock_message or "Registration is currently unavailable"
                )
            else:
                try:
                    with transaction.atomic():
                        saved = save_registration(
                            reg_form, team_formset, judge_formset, registration
                        )
                except forms.ValidationError as error:
                    team_formset._non_form_errors = (  # pylint: disable=protected-access
                        team_formset.error_class(error.messages)
                    )
                else:
                    try:
                        send_registration_confirmation_email(saved, request)
                    except (ImproperlyConfigured, EmailServiceError) as error:
                        message = (
                            "Registration saved, but the confirmation email "
                            f"could not be sent: {error}"
                        )
                        messages.warning(
                            request,
                            message,
                        )
                    try:
                        send_registration_team_portal_emails(saved, request)
                    except (ImproperlyConfigured, EmailServiceError) as error:
                        message = (
                            "Registration saved, but team portal email(s) "
                            f"could not be sent: {error}"
                        )
                        messages.warning(
                            request,
                            message,
                        )
                    try:
                        send_registration_judge_code_emails(saved, request)
                    except (ImproperlyConfigured, EmailServiceError) as error:
                        message = (
                            "Registration saved, but judge code email(s) "
                            f"could not be sent: {error}"
                        )
                        messages.warning(
                            request,
                            message,
                        )
                    saved_url = reverse(
                        "registration_portal_edit", args=[saved.herokunator_code]
                    )
                    return redirect(f"{saved_url}?saved=1")
    summary = None
    if registration:
        summary = (
            Registration.objects.select_related("school")
            .prefetch_related(
                Prefetch(
                    "teams",
                    queryset=Team.objects.select_related(
                        "school", "hybrid_school"
                    ).prefetch_related("debaters"),
                ),
                Prefetch(
                    "judges",
                    queryset=Judge.objects.prefetch_related("schools"),
                ),
            )
            .get(pk=registration.pk)
        )
    registration_saved = request.GET.get("saved") == "1" and bool(summary)
    post_reg_links = (
        list(RegistrationLink.objects.filter(is_active=True))
        if registration_saved
        else []
    )
    context = {
        "registration_form": reg_form,
        "team_formset": team_formset,
        "judge_formset": judge_formset,
        "config": config,
        "summary": summary,
        "registration_saved": registration_saved,
        "post_registration_links": post_reg_links,
        "max_teams": MAX_TEAMS,
        "is_edit_mode": is_edit_mode,
        "can_create_registration": can_create,
        "can_modify_registration": can_modify,
        "can_submit_registration": can_submit,
        "registration_lock_message": lock_message,
    }
    return render(request, "registration/portal.html", context)


@permission_required("tab.tab_settings.can_change", login_url="/403/")
@require_http_methods(["GET", "POST"])
def registration_admin(request):
    config = RegistrationConfig.get_or_create_active()
    if request.method == "POST" and request.POST.get("registration_id"):
        reg_id = request.POST.get("registration_id")
        registration = Registration.objects.filter(pk=reg_id).first()
        if not registration:
            return redirect_and_flash_error(
                request,
                "Registration not found.",
                path=reverse("registration_admin"),
            )
        log_registration_deleted(registration)
        registration.delete()
        invalidate_all_public_caches()
        return redirect_and_flash_success(
            request,
            "Registration and all associated data deleted.",
            path=reverse("registration_admin"),
        )

    form = RegistrationSettingsForm(
        request.POST or None,
        config=config,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        invalidate_all_public_caches()
        return redirect_and_flash_success(
            request,
            "Registration settings updated!",
            path=reverse("registration_admin"),
        )

    registrations = (
        Registration.objects.select_related("school")
        .prefetch_related(
            Prefetch(
                "teams",
                queryset=Team.objects.select_related(
                    "school", "hybrid_school"
                ).prefetch_related("debaters"),
            ),
            Prefetch(
                "judges",
                queryset=Judge.objects.prefetch_related("schools"),
            ),
            "change_logs",
        )
        .annotate(team_count=Count("teams"), judge_count=Count("judges"))
        .order_by("-created_at")
    )
    recent_changes = RegistrationChangeLog.objects.all()[:25]
    return render(
        request,
        "registration/admin_list.html",
        {
            "registrations": registrations,
            "form": form,
            "config": config,
            "recent_changes": recent_changes,
            "registration_links": list(RegistrationLink.objects.all()),
        },
    )


@permission_required("tab.tab_settings.can_change", login_url="/403/")
@require_http_methods(["POST"])
def tournament_link_create(request, link_kind):
    config = _link_kind_or_404(link_kind)
    form = config["form"](request.POST)
    if form.is_valid():
        form.save()
        invalidate_all_public_caches()
        return redirect_and_flash_success(
            request,
            f"{config['label'].capitalize()} link added.",
            path=reverse(config["redirect_url_name"]),
        )
    return redirect_and_flash_error(
        request,
        _summarize_form_errors(form, f"Could not add {config['label']} link."),
        path=reverse(config["redirect_url_name"]),
    )


@permission_required("tab.tab_settings.can_change", login_url="/403/")
@require_http_methods(["POST"])
def tournament_link_update(request, link_kind, link_id):
    config = _link_kind_or_404(link_kind)
    link = config["model"].objects.filter(pk=link_id).first()
    if not link:
        raise Http404()
    form = config["form"](request.POST, instance=link)
    if form.is_valid():
        form.save()
        invalidate_all_public_caches()
        return redirect_and_flash_success(
            request,
            f"{config['label'].capitalize()} link updated.",
            path=reverse(config["redirect_url_name"]),
        )
    return redirect_and_flash_error(
        request,
        _summarize_form_errors(form, f"Could not update {config['label']} link."),
        path=reverse(config["redirect_url_name"]),
    )


@permission_required("tab.tab_settings.can_change", login_url="/403/")
@require_http_methods(["POST"])
def tournament_link_delete(request, link_kind, link_id):
    config = _link_kind_or_404(link_kind)
    link = config["model"].objects.filter(pk=link_id).first()
    if not link:
        raise Http404()
    link.delete()
    invalidate_all_public_caches()
    return redirect_and_flash_success(
        request,
        f"{config['label'].capitalize()} link removed.",
        path=reverse(config["redirect_url_name"]),
    )


def _summarize_form_errors(form, fallback):
    parts = []
    for field, errors in form.errors.items():
        label = form.fields[field].label or field.replace("_", " ").title()
        for error in errors:
            parts.append(f"{label}: {error}")
    return " ".join(parts) if parts else fallback


@require_http_methods(["GET", "POST"])
def team_portal_search(request):
    if request.method == "POST":
        team_code = (request.POST.get("team_code") or "").strip()
        if team_code:
            team = Team.objects.filter(team_code__iexact=team_code).first()
            if team:
                return redirect("team_portal", team_code=team.team_code)
            return redirect_and_flash_error(
                request,
                (
                    "We couldn't find a team for that code. "
                    "Double-check and try again."
                ),
                path=reverse("team_portal_search"),
            )
        return redirect_and_flash_error(
            request,
            "Please enter the team code provided by tab.",
            path=reverse("team_portal_search"),
        )

    return render(request, "registration/team_portal_search.html")


def _team_for_code(team_code):
    return (
        Team.objects.select_related("school", "registration")
        .prefetch_related("debaters__school", "scratches__judge")
        .filter(team_code=team_code)
        .first()
    )


EDITS_LOCKED_MESSAGE = (
    "Edits are auto-disabled once round 1 has been paired. "
    "Please contact the tab room if you need to make a change."
)


def _edits_locked_by_pairings():
    return int(TabSettings.get("cur_round", 1) or 1) > 1


def _team_round_history(team):
    cur_round = int(TabSettings.get("cur_round", 1) or 1)
    # Show only completed previous rounds — exclude the current round (the
    # most recently paired one), which the team can see on public pairings.
    max_visible = cur_round - 2
    if max_visible < 1:
        return []

    rounds = (
        Round.objects.filter(round_number__lte=max_visible)
        .filter(Q(gov_team=team) | Q(opp_team=team))
        .select_related("gov_team", "opp_team", "chair", "room")
        .prefetch_related(
            "judges",
            "gov_team__debaters",
            "opp_team__debaters",
        )
        .order_by("round_number")
    )
    history = []
    for rnd in rounds:
        is_gov = rnd.gov_team_id == team.id
        history.append({
            "round_number": rnd.round_number,
            "side": "Gov" if is_gov else "Opp",
            "opponent": rnd.opp_team if is_gov else rnd.gov_team,
            "judges": list(rnd.judges.all()),
            "chair": rnd.chair,
            "room": rnd.room,
        })
    return history


@require_http_methods(["GET", "POST"])
def team_portal(request, team_code):
    team = _team_for_code(team_code)
    if not team:
        raise Http404()

    config = RegistrationConfig.get_or_create_active()
    edits_locked_by_pairings = _edits_locked_by_pairings()
    name_form = TeamNameChangeForm(team=team)
    scratch_form = TeamPortalScratchForm(
        team=team,
        quantity=config.disc_scratch_quantity,
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_name":
            name_form = TeamNameChangeForm(request.POST, team=team)
            if edits_locked_by_pairings:
                name_form.add_error(None, EDITS_LOCKED_MESSAGE)
            elif not config.team_name_changes_allowed:
                name_form.add_error(None, "Team name changes are currently disabled.")
            elif name_form.is_valid():
                name_form.save()
                invalidate_all_public_caches()
                return redirect_and_flash_success(
                    request,
                    "Team name updated.",
                    path=reverse("team_portal", args=[team.team_code]),
                )
        elif action == "update_scratches":
            scratch_form = TeamPortalScratchForm(
                request.POST,
                team=team,
                quantity=config.disc_scratch_quantity,
            )
            if edits_locked_by_pairings:
                scratch_form.add_error(None, EDITS_LOCKED_MESSAGE)
            elif not config.disc_scratches_open:
                scratch_form.add_error(None, "Disc scratches are currently closed.")
            elif scratch_form.is_valid():
                with transaction.atomic():
                    scratch_form.save()
                invalidate_all_public_caches()
                return redirect_and_flash_success(
                    request,
                    "Disc scratches updated.",
                    path=reverse("team_portal", args=[team.team_code]),
                )
        else:
            messages.error(request, "Unknown team portal action.")

    scratches = (
        Scratch.objects.filter(team=team, scratch_type=Scratch.TEAM_SCRATCH)
        .select_related("judge")
        .order_by("judge__name")
    )
    return render(
        request,
        "registration/team_portal.html",
        {
            "team": team,
            "config": config,
            "name_form": name_form,
            "scratch_form": scratch_form,
            "scratches": scratches,
            "edits_locked_by_pairings": edits_locked_by_pairings,
            "team_round_history": _team_round_history(team),
        },
    )


@require_http_methods(["GET"])
def proxy_debaters(request, school_id):
    """Proxy endpoint for school debaters to avoid CORS issues."""
    try:
        url = f"{settings.BLACK_ROD_API_BASE_URL}/api/debaters/{school_id}/"
        response = requests.get(url, timeout=10)
        if response.ok:
            return JsonResponse(response.json(), safe=False)
        return JsonResponse(
            {"error": "Failed to fetch debaters"}, status=response.status_code
        )
    except RequestException:
        logger.exception(
            "Failed to fetch debaters from upstream API for school_id=%s",
            school_id,
        )
        return JsonResponse({"error": "Failed to fetch debaters"}, status=500)


def _school_debater_ids(school_id):
    """Return APDA debater IDs from the public school debater list."""
    url = f"{settings.BLACK_ROD_API_BASE_URL}/api/debaters/{school_id}/"
    try:
        response = requests.get(url, timeout=10)
        if not response.ok:
            return set()
        payload = response.json()
    except (ValueError, RequestException):
        logger.exception(
            "Failed to fetch debaters from upstream API for email status school_id=%s",
            school_id,
        )
        return set()
    entries = payload if isinstance(payload, list) else payload.get("debaters", [])
    debater_ids = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_id = (
            entry.get("apda_id")
            if entry.get("apda_id") is not None
            else entry.get("id")
        )
        try:
            debater_ids.add(int(raw_id))
        except (TypeError, ValueError):
            continue
    return debater_ids


@require_http_methods(["POST"])
def proxy_debater_email_status(request):
    """Return only masked private email availability for a school-scoped debater."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    try:
        debater_id = int(payload.get("debater_id"))
        school_id = int(payload.get("school_id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid debater or school"}, status=400)

    if debater_id not in _school_debater_ids(school_id):
        return JsonResponse(
            {"id": debater_id, "has_email": False, "masked_email": ""},
            status=404,
        )

    email = fetch_private_debater_emails([debater_id]).get(debater_id)
    return JsonResponse(
        {
            "id": debater_id,
            "has_email": bool(email),
            "masked_email": mask_private_email(email),
        }
    )
