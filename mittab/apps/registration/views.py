from typing import Type, cast

from django import forms
from django.contrib.auth.decorators import permission_required
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch
from django.forms import BaseFormSet, formset_factory
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.utils.functional import cached_property
import requests
from requests.exceptions import RequestException

from mittab.apps.registration.forms import (
    JudgeForm,
    NEW_CHOICE_VALUE,
    RegistrationForm,
    RegistrationSettingsForm,
    TeamForm,
)
from mittab.apps.tab.models import CheckIn, Debater, Judge, School, TabSettings, Team
from mittab.apps.tab.helpers import (
    redirect_and_flash_error,
    redirect_and_flash_success,
)
from mittab.libs.cacheing.public_cache import invalidate_all_public_caches
from .models import Registration, RegistrationConfig

SCHOOL_ACTIVE_URL = "https://results.apda.online/api/schools/all/"
SCHOOL_ALL_URL = SCHOOL_ACTIVE_URL
MAX_TEAMS = 200


class RegistrationTeamFormSet(BaseFormSet):
    def __init__(self, *args, school_choices=None, **kwargs):
        self.school_choices = school_choices or []
        super().__init__(*args, **kwargs)

    def _construct_form(self, i, **kwargs):
        kwargs["school_choices"] = self.school_choices
        return super()._construct_form(i, **kwargs)

    def clean(self):
        super().clean()


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

    @cached_property
    def empty_form(self):
        form = self.form(
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


def fetch_remote_schools():
    """Fetch schools from the API."""
    results = []
    seen = set()
    try:
        response = requests.get(SCHOOL_ACTIVE_URL, timeout=5)
        response.raise_for_status()
        items = response.json()
    except (ValueError, RequestException):
        items = []
    for item in items if isinstance(items, list) else items.get("schools", []):
        name = item.get("name")
        apda_id = item.get("id") if "id" in item else item.get("apda_id")
        if not name or apda_id is None or apda_id in seen:
            continue
        seen.add(apda_id)
        results.append({"id": apda_id, "name": name})
    return results


def build_school_choices():
    results = []
    seen_values = set()

    def add_choice(value, label):
        if not value or value in seen_values:
            return
        seen_values.add(value)
        results.append((value, label))

    for school in fetch_remote_schools():
        add_choice(f"apda:{school['id']}", school["name"])

    for school in School.objects.order_by("name").only("pk", "name", "apda_id"):
        if school.apda_id not in (None, -1):
            value = f"apda:{school.apda_id}"
        else:
            value = f"pk:{school.pk}"
        add_choice(value, school.name)

    results.sort(key=lambda item: (item[1] or "").lower())
    return results


def get_round_config():
    try:
        total_rounds = int(TabSettings.get("tot_rounds", 6))
    except (TypeError, ValueError):
        total_rounds = 6
    total_rounds = max(total_rounds, 0)
    return {
        "outround": 0,
        "prelims": list(range(1, total_rounds + 1)),
    }


def sync_judge_checkins(judge, desired_rounds):
    desired = set(desired_rounds)
    existing = set(
        CheckIn.objects.filter(judge=judge).values_list("round_number", flat=True)
    )
    additions = [
        CheckIn(judge=judge, round_number=round_number)
        for round_number in desired - existing
    ]
    removals = existing - desired
    if additions:
        CheckIn.objects.bulk_create(additions, ignore_conflicts=True)
    if removals:
        CheckIn.objects.filter(
            judge=judge, round_number__in=removals
        ).delete()


def school_value(school):
    if not school:
        return ""
    if school.apda_id not in (None, -1):
        return f"apda:{school.apda_id}"
    return f"pk:{school.pk}"


def registration_team_initial(team):
    members = list(team.debaters.all())
    defaults = [{"debater": None, "school": None} for _ in range(2)]
    for index in range(2):
        debater = members[index] if len(members) > index else None
        defaults[index] = {
            "debater": debater,
            "school": getattr(debater, "school", None),
        }
    first_school = defaults[0]["school"]
    second_school = defaults[1]["school"]
    primary_source = "debater_one"
    if team.school and second_school and team.school.pk == second_school.pk:
        primary_source = "debater_two"
    elif team.school and first_school and team.school.pk == first_school.pk:
        primary_source = "debater_one"
    return {
        "team_id": team.pk,
        "name": team.name,
        "seed_choice": team.seed,
        "team_school_source": primary_source,
        "debater_one_id": defaults[0]["debater"].pk if defaults[0]["debater"] else None,
        "debater_one_name": (
            defaults[0]["debater"].name if defaults[0]["debater"] else ""
        ),
        "debater_one_apda_id": (
            defaults[0]["debater"].apda_id if defaults[0]["debater"] else None
        ),
        "debater_one_novice_status": (
            defaults[0]["debater"].novice_status if defaults[0]["debater"] else 0
        ),
        "debater_one_qualified": (
            defaults[0]["debater"].qualified if defaults[0]["debater"] else False
        ),
        "debater_one_school": school_value(first_school),
        "debater_one_school_name": first_school.name if first_school else "",
        "debater_two_id": defaults[1]["debater"].pk if defaults[1]["debater"] else None,
        "debater_two_name": (
            defaults[1]["debater"].name if defaults[1]["debater"] else ""
        ),
        "debater_two_apda_id": (
            defaults[1]["debater"].apda_id if defaults[1]["debater"] else None
        ),
        "debater_two_novice_status": (
            defaults[1]["debater"].novice_status if defaults[1]["debater"] else 0
        ),
        "debater_two_qualified": (
            defaults[1]["debater"].qualified if defaults[1]["debater"] else False
        ),
        "debater_two_school": school_value(second_school),
        "debater_two_school_name": second_school.name if second_school else "",
    }


def registration_judge_initial(judge, round_config):
    checkins = {checkin.round_number for checkin in judge.checkin_set.all()}
    affiliated = list(judge.schools.all())
    initial = {
        "registration_judge_id": judge.pk,
        "judge_id": judge.pk,
        "name": judge.name,
        "email": judge.email,
        "experience": judge.rank,
        "schools": [school_value(school) for school in affiliated],
        "school_label_map": {
            school_value(school): school.name for school in affiliated
        },
    }
    outround = round_config.get("outround")
    if outround is not None:
        initial["availability_outround"] = outround in checkins
    for round_number in round_config.get("prelims", []):
        initial[f"availability_round_{round_number}"] = round_number in checkins
    return initial


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
            for judge in registration.judges.prefetch_related("checkin_set", "schools")
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


def resolve_school(selection, cache=None):
    if not selection:
        raise forms.ValidationError("Select a school")
    cache = cache or {}
    key = tuple(sorted(selection.items()))
    if key in cache:
        return cache[key]
    if "pk" in selection:
        school = School.objects.filter(pk=selection["pk"]).first()
        if school:
            cache[key] = school
            return school
        raise forms.ValidationError("Unknown school")
    if "apda_id" in selection:
        school, _ = School.objects.get_or_create(
            apda_id=selection["apda_id"],
            defaults={"name": selection.get("name", "")},
        )
        cache[key] = school
        return school
    name = selection.get("name", "").strip()
    if not name:
        raise forms.ValidationError("Enter a school name")
    try:
        school, _ = School.objects.get_or_create(
            name__iexact=name,
            defaults={"name": name},
        )
    except IntegrityError:
        existing = School.objects.filter(name__iexact=name).first()
        if existing:
            school = existing
        else:
            raise forms.ValidationError("School already exists, select it instead")
    cache[key] = school
    return school


def uniquify_name(model_cls, desired_name, *, field_name="name", exclude_pk=None):
    base = (desired_name or "").strip()
    field = model_cls._meta.get_field(field_name)
    max_length = getattr(field, "max_length", None)

    def exists(candidate):
        query = {f"{field_name}__iexact": candidate}
        qs = model_cls.objects.filter(**query)
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return qs.exists()

    if base and not exists(base):
        return base

    counter = 1
    while True:
        suffix = f" ({counter})"
        truncated = base or "Entry"
        if max_length:
            truncated = truncated[: max_length - len(suffix)].rstrip()
        candidate = f"{truncated}{suffix}"
        if not exists(candidate):
            return candidate
        counter += 1


def get_or_create_debater(data, school):
    name = (data["name"] or "").strip()
    if not name:
        raise forms.ValidationError("Debater name required")
    debater = (
        Debater.objects.filter(pk=data.get("id")).first()
        if data.get("id")
        else Debater()
    )
    debater.name = uniquify_name(Debater, name, exclude_pk=debater.pk)
    debater.novice_status = data["novice_status"]
    debater.qualified = data["qualified"]
    debater.apda_id = data.get("apda_id") or -1
    debater.school = school
    try:
        debater.save()
    except IntegrityError:
        debater.name = uniquify_name(Debater, name, exclude_pk=debater.pk)
        debater.save()
    return debater


def save_registration(reg_form, team_formset, judge_formset, registration):
    school = resolve_school(reg_form.get_school())
    registration = registration or Registration()
    registration.school = school
    registration.email = reg_form.cleaned_data["email"]
    registration.save()

    school_cache = {}
    saved_team_ids = []
    for form in team_formset:
        if form.cleaned_data.get("DELETE"):
            continue
        payload = form.get_payload()
        members = payload["members"]
        member_schools = [resolve_school(member["school"]) for member in members]
        primary_index = 0 if payload["team_school_source"] == "debater_one" else 1
        primary_school = member_schools[primary_index]
        if primary_school != school:
            raise forms.ValidationError(
                "The primary debater must represent the registration school"
            )
        team = (
            Team.objects.filter(
                pk=payload.get("team_id"), registration=registration
            ).first()
            or Team(registration=registration)
        )
        team.name = uniquify_name(Team, payload["name"], exclude_pk=team.pk)
        team.seed = payload["seed_choice"]
        team.school = primary_school
        team.hybrid_school = None
        team.break_preference = Team.VARSITY
        team.checked_in = False
        try:
            team.save()
        except IntegrityError:
            team.name = uniquify_name(Team, payload["name"], exclude_pk=team.pk)
            team.save()
        debaters = [
            get_or_create_debater(member, member_schools[index])
            for index, member in enumerate(members)
        ]
        team.debaters.set(debaters)
        saved_team_ids.append(team.pk)
    for team in registration.teams.exclude(pk__in=saved_team_ids):
        team.debaters.clear()
        team.delete()

    saved_judge_ids = []
    for form in judge_formset:
        if form.cleaned_data.get("DELETE"):
            continue
        payload = form.get_payload()
        judge = Judge.objects.filter(
            pk=payload.get("judge_id"), registration=registration
        ).first() or Judge(registration=registration)
        judge.name = uniquify_name(Judge, payload["name"], exclude_pk=judge.pk)
        judge.rank = payload["experience"]
        judge.email = payload["email"]
        try:
            judge.save()
        except IntegrityError:
            judge.name = uniquify_name(Judge, payload["name"], exclude_pk=judge.pk)
            judge.save()
        selected_schools = payload["schools"] or [{"pk": registration.school.pk}]
        resolved_schools = [
            resolve_school(selection, cache=school_cache)
            for selection in selected_schools
        ]
        judge.schools.set(resolved_schools)
        sync_judge_checkins(judge, payload["availability_rounds"])
        saved_judge_ids.append(judge.pk)
    for judge in registration.judges.exclude(pk__in=saved_judge_ids):
        judge.delete()
    return registration


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
                "We couldn't find a registration for that code. Double-check and try again.",
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
                team_formset._non_form_errors = team_formset.error_class(  # pylint: disable=protected-access
                    [message]
                )
                judge_formset._non_form_errors = judge_formset.error_class(  # pylint: disable=protected-access
                    [message]
                )
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
                    return redirect(
                        reverse(
                            "registration_portal_edit", args=[saved.herokunator_code]
                        )
                    )
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
        "max_teams": MAX_TEAMS,
        "is_edit_mode": is_edit_mode,
        "can_create_registration": can_create,
        "can_modify_registration": can_modify,
        "can_submit_registration": can_submit,
        "registration_lock_message": lock_message,
    }
    return render(request, "registration/portal.html", context)


@permission_required("tab.tab_settings.can_change", login_url="/403/")
def registration_setup(request):
    return redirect("registration_admin")


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
                queryset=Team.objects.select_related("school", "hybrid_school").prefetch_related(
                    "debaters"
                ),
            ),
            Prefetch(
                "judges",
                queryset=Judge.objects.prefetch_related("schools"),
            ),
        )
        .annotate(team_count=Count("teams"), judge_count=Count("judges"))
        .order_by("-created_at")
    )
    return render(
        request,
        "registration/admin_list.html",
        {
            "registrations": registrations,
            "form": form,
            "config": config,
        },
    )


@require_http_methods(["GET"])
def proxy_schools_active(request):
    """Proxy endpoint for active schools to avoid CORS issues."""
    try:
        response = requests.get(SCHOOL_ACTIVE_URL, timeout=10)
        if response.ok:
            return JsonResponse(response.json(), safe=False)
        return JsonResponse(
            {"error": "Failed to fetch schools"}, status=response.status_code
        )
    except RequestException as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["GET"])
def proxy_schools_all(request):
    """Proxy endpoint for all schools to avoid CORS issues."""
    try:
        response = requests.get(SCHOOL_ALL_URL, timeout=10)
        if response.ok:
            return JsonResponse(response.json(), safe=False)
        return JsonResponse(
            {"error": "Failed to fetch schools"}, status=response.status_code
        )
    except RequestException as e:
        return JsonResponse({"error": str(e)}, status=500)


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
    except RequestException as e:
        return JsonResponse({"error": str(e)}, status=500)
