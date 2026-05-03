import logging
from typing import Type, cast

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Count, Prefetch
from django.forms import BaseFormSet, formset_factory
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
import requests
from requests.exceptions import RequestException

from mittab.apps.registration.forms import (
    JudgeForm,
    RegistrationForm,
    RegistrationSettingsForm,
    TeamForm,
)
from mittab.apps.registration.emails import (
    send_registration_confirmation_email,
    send_registration_judge_code_emails,
)
from mittab.apps.registration.services import (
    build_school_choices,
    get_round_config,
    log_registration_deleted,
    registration_judge_initial,
    registration_team_initial,
    save_registration,
    school_value,
)
from mittab.apps.tab.models import Judge, Team
from mittab.apps.tab.helpers import (
    redirect_and_flash_error,
    redirect_and_flash_success,
)
from mittab.libs.cacheing.public_cache import invalidate_all_public_caches
from mittab.libs.email_service import EmailServiceError
from .models import Registration, RegistrationChangeLog, RegistrationConfig

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
    context = {
        "registration_form": reg_form,
        "team_formset": team_formset,
        "judge_formset": judge_formset,
        "config": config,
        "summary": summary,
        "registration_saved": request.GET.get("saved") == "1" and bool(summary),
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
        },
    )

@require_http_methods(["GET"])
def proxy_debaters(request, school_id):
    """Proxy endpoint for school debaters to avoid CORS issues."""
    try:
        url = f"https://results.apda.online/api/debaters/{school_id}/"
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
