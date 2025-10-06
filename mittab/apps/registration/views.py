from django import forms
from django.db import transaction
from django.db.models import Prefetch
from django.forms import BaseFormSet, formset_factory
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
import requests
from requests.exceptions import RequestException

from mittab.apps.registration.forms import (
    JudgeForm,
    NEW_CHOICE_VALUE,
    RegistrationForm,
    TeamForm,
    parse_school,
)
from mittab.apps.tab.models import Debater, Judge, School, Team
from .models import (
    Registration,
    RegistrationConfig,
    RegistrationJudge,
    RegistrationTeam,
    RegistrationTeamMember,
)

SCHOOL_ACTIVE_URL = "https://results.apda.online/api/schools/"
SCHOOL_ALL_URL = "https://results.apda.online/api/schools/all/"
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
        if any(self.errors):
            return
        active = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        if not active:
            raise forms.ValidationError("Add at least one team")
        free = sum(1 for form in active if form.cleaned_data.get("is_free_seed"))
        if free != 1:
            raise forms.ValidationError("Select exactly one free seed")


class RegistrationJudgeFormSet(BaseFormSet):
    pass


TeamFormSet = formset_factory(
    TeamForm,
    formset=RegistrationTeamFormSet,
    extra=0,
    can_delete=True,
    max_num=MAX_TEAMS,
)
JudgeFormSet = formset_factory(
    JudgeForm,
    formset=RegistrationJudgeFormSet,
    extra=0,
    can_delete=True,
)


def fetch_remote_schools(active_only=True):
    """
    Fetch schools from the API.
    If active_only is True, only fetches from the active schools endpoint.
    If False, fetches from both active and all schools endpoints.
    """
    results = []
    seen = set()
    urls = (SCHOOL_ACTIVE_URL,) if active_only else (SCHOOL_ACTIVE_URL, SCHOOL_ALL_URL)
    for url in urls:
        try:
            response = requests.get(url, timeout=5)
            if not response.ok:
                continue
            data = response.json()
        except (ValueError, RequestException):
            continue
        items = data if isinstance(data, list) else data.get("schools", [])
        for item in items:
            name = item.get("name")
            apda_id = item.get("id") if "id" in item else item.get("apda_id")
            if not name or apda_id is None or apda_id in seen:
                continue
            seen.add(apda_id)
            results.append({"id": apda_id, "name": name})
    return results


def build_school_choices(active_only=True):
    """Build school choices for the dropdown."""
    return [(f"apda:{school['id']}", school["name"]) for school in fetch_remote_schools(active_only)]


def school_value(school):
    if not school:
        return ""
    if school.apda_id not in (None, -1):
        return f"apda:{school.apda_id}"
    return NEW_CHOICE_VALUE


def registration_team_initial(reg_team):
    team = reg_team.team
    members = list(reg_team.members.select_related("debater", "school").order_by("position"))
    defaults = [{"debater": None, "school": None}, {"debater": None, "school": None}]
    for member in members:
        if member.position in (0, 1):
            defaults[member.position] = {"debater": member.debater, "school": member.school}
    for index in range(2):
        if defaults[index]["debater"] is None:
            debater = team.debaters.all()[index] if team.debaters.count() > index else None
            defaults[index]["debater"] = debater
            defaults[index]["school"] = team.hybrid_school if index == 1 else team.school
    first_school = defaults[0]["school"]
    second_school = defaults[1]["school"]
    return {
        "registration_team_id": reg_team.pk,
        "team_id": team.pk,
        "name": team.name,
        "is_free_seed": reg_team.is_free_seed,
        "debater_one_id": defaults[0]["debater"].pk if defaults[0]["debater"] else None,
        "debater_one_name": defaults[0]["debater"].name if defaults[0]["debater"] else "",
        "debater_one_apda_id": defaults[0]["debater"].apda_id if defaults[0]["debater"] else None,
        "debater_one_novice_status": defaults[0]["debater"].novice_status if defaults[0]["debater"] else 0,
        "debater_one_qualified": defaults[0]["debater"].qualified if defaults[0]["debater"] else False,
        "debater_one_school": school_value(first_school),
        "debater_one_school_name": first_school.name if first_school else "",
        "debater_two_id": defaults[1]["debater"].pk if defaults[1]["debater"] else None,
        "debater_two_name": defaults[1]["debater"].name if defaults[1]["debater"] else "",
        "debater_two_apda_id": defaults[1]["debater"].apda_id if defaults[1]["debater"] else None,
        "debater_two_novice_status": defaults[1]["debater"].novice_status if defaults[1]["debater"] else 0,
        "debater_two_qualified": defaults[1]["debater"].qualified if defaults[1]["debater"] else False,
        "debater_two_school": school_value(second_school),
        "debater_two_school_name": second_school.name if second_school else "",
    }


def registration_judge_initial(reg_judge):
    judge = reg_judge.judge
    return {
        "registration_judge_id": reg_judge.pk,
        "judge_id": judge.pk,
        "name": judge.name,
        "experience": judge.rank,
    }


def get_registration_forms(request, registration, school_choices):
    if request.method == "POST":
        reg_form = RegistrationForm(request.POST, school_choices=school_choices)
        team_formset = TeamFormSet(request.POST, prefix="teams", school_choices=school_choices)
        judge_formset = JudgeFormSet(request.POST, prefix="judges")
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
            for team in registration.teams.select_related("team").prefetch_related("team__debaters", "members__debater", "members__school")
        ]
        judges_initial = [
            registration_judge_initial(reg)
            for reg in registration.judges.select_related("judge")
        ]
    else:
        reg_form = RegistrationForm(school_choices=school_choices)
        teams_initial = []
        judges_initial = []
    team_formset = TeamFormSet(
        initial=teams_initial,
        prefix="teams",
        school_choices=school_choices,
    )
    judge_formset = JudgeFormSet(initial=judges_initial, prefix="judges")
    return reg_form, team_formset, judge_formset


def resolve_school(selection, cache):
    key = tuple(sorted(selection.items()))
    if key in cache:
        return cache[key]
    if "pk" in selection:
        school = School.objects.select_for_update().filter(pk=selection["pk"]).first()
        if not school:
            raise forms.ValidationError("Unknown school")
        cache[key] = school
        return school
    if "apda_id" in selection:
        school = School.objects.select_for_update().filter(apda_id=selection["apda_id"]).first()
        if school:
            cache[key] = school
            return school
        school = School.objects.create(name=selection.get("name", ""), apda_id=selection["apda_id"])
        cache[key] = school
        return school
    name = selection["name"]
    school = School.objects.select_for_update().filter(name__iexact=name).first()
    if school:
        if school.apda_id in (None, 0):
            school.apda_id = -1
            school.save()
        cache[key] = school
        return school
    school = School.objects.create(name=name, apda_id=-1)
    cache[key] = school
    return school


def get_or_create_debater(data, school, team):
    debater_id = data.get("id")
    apda_id = data.get("apda_id")
    name = data["name"].strip()
    if not name:
        raise forms.ValidationError("Debater name required")
    queryset = Debater.objects.select_for_update()
    debater = None
    if debater_id:
        debater = queryset.filter(pk=debater_id).first()
        if not debater:
            raise forms.ValidationError("Unknown debater")
    elif apda_id not in (None, ""):
        debater = queryset.filter(apda_id=apda_id).first()
    if not debater:
        debater = queryset.filter(name__iexact=name).first()
    if not debater:
        debater = Debater(name=name, novice_status=data["novice_status"], apda_id=-1)
    debater.name = name
    debater.novice_status = data["novice_status"]
    debater.qualified = data["qualified"]
    debater.apda_id = apda_id if apda_id not in (None, "") else -1
    debater.save()
    assignments = debater.team_set.exclude(pk=team.pk if team else None)
    if assignments.exists():
        raise forms.ValidationError(f"Debater {debater.name} is already on another team")
    return debater


def ensure_unique_team_name(team, name):
    conflict = Team.objects.filter(name__iexact=name)
    if team.pk:
        conflict = conflict.exclude(pk=team.pk)
    if conflict.exists():
        raise forms.ValidationError(f"Team name {name} already exists")


def summarise_registration(registration):
    if not registration:
        return None
    registration = Registration.objects.select_related("school").prefetch_related(
        Prefetch(
            "teams",
            queryset=RegistrationTeam.objects.select_related("team").prefetch_related(
                "team__debaters",
                "members__debater",
                "members__school",
            ),
        ),
        "judges__judge",
    ).get(pk=registration.pk)
    teams = []
    for reg_team in registration.teams.all():
        team = reg_team.team
        debaters = []
        for member in reg_team.members.select_related("debater", "school").order_by("position"):
            debaters.append(member.debater.name)
        if not debaters:
            debaters = [debater.name for debater in team.debaters.all()]
        teams.append({"name": team.name, "is_free_seed": reg_team.is_free_seed, "debaters": debaters})
    judges = [
        {"name": reg.judge.name, "code": reg.judge.ballot_code}
        for reg in registration.judges.select_related("judge")
    ]
    return {
        "code": registration.herokunator_code,
        "email": registration.email,
        "school": registration.school.name,
        "teams": teams,
        "judges": judges,
    }


def save_registration(reg_form, team_formset, judge_formset, registration):
    school_cache = {}
    main_school = resolve_school(reg_form.get_school(), school_cache)
    registration = registration or Registration()
    registration.school = main_school
    registration.email = reg_form.cleaned_data["email"]
    registration.save()
    team_map = {
        team.pk: team
        for team in registration.teams.select_related("team").prefetch_related("team__debaters", "members__debater", "members__school")
    }
    saved_team_ids = []
    for form in team_formset:
        if form.cleaned_data.get("DELETE"):
            continue
        payload = form.get_payload()
        team_obj = Team.objects.select_for_update().filter(pk=payload["team_id"]).first() if payload.get("team_id") else Team()
        ensure_unique_team_name(team_obj, payload["name"])
        members = payload["members"]
        first_school = resolve_school(members[0]["school"], school_cache)
        if first_school.pk != main_school.pk:
            raise forms.ValidationError("The first debater must represent the registration school")
        hybrid_school = None
        member_instances = []
        for member_payload in members:
            member_school = resolve_school(member_payload["school"], school_cache)
            if member_school.pk != main_school.pk and not hybrid_school:
                hybrid_school = member_school
            debater = get_or_create_debater(member_payload, member_school, team_obj if team_obj.pk else None)
            member_instances.append((debater, member_school))
        team_obj.school = main_school
        team_obj.hybrid_school = hybrid_school
        team_obj.name = payload["name"]
        team_obj.seed = Team.FREE_SEED if payload["is_free_seed"] else Team.UNSEEDED
        team_obj.break_preference = Team.VARSITY
        team_obj.checked_in = False
        team_obj.save()
        team_obj.debaters.set([debater for debater, _ in member_instances])
        reg_team = RegistrationTeam.objects.select_for_update().filter(pk=payload.get("registration_team_id"), registration=registration).first()
        if not reg_team:
            reg_team = RegistrationTeam.objects.create(registration=registration, team=team_obj)
        reg_team.is_free_seed = payload["is_free_seed"]
        reg_team.team = team_obj
        reg_team.save()
        member_map = {member.position: member for member in reg_team.members.select_for_update()}
        for index, (debater, school) in enumerate(member_instances):
            member = member_map.pop(index, None)
            if not member:
                member = RegistrationTeamMember(registration_team=reg_team, position=index)
            member.debater = debater
            member.school = school
            member.save()
        for leftover in member_map.values():
            leftover.delete()
        saved_team_ids.append(reg_team.pk)
    for reg_team in list(registration.teams.all()):
        if reg_team.pk not in saved_team_ids:
            team = reg_team.team
            reg_team.members.all().delete()
            reg_team.delete()
            team.debaters.clear()
            try:
                team.delete()
            except Exception:
                pass
    judge_map = {
        judge.pk: judge
        for judge in registration.judges.select_related("judge")
    }
    saved_judge_ids = []
    for form in judge_formset:
        if form.cleaned_data.get("DELETE"):
            continue
        payload = form.get_payload()
        experience = payload["experience"]
        if experience < 0 or experience > 10:
            raise forms.ValidationError("Judge experience must be between 0 and 10")
        judge = Judge.objects.select_for_update().filter(pk=payload.get("judge_id")).first()
        if not judge:
            judge = Judge(name=payload["name"], rank=experience)
        judge.name = payload["name"]
        judge.rank = experience
        judge.save()
        relation = registration.judges.select_for_update().filter(pk=payload.get("registration_judge_id"), registration=registration).first()
        if not relation:
            relation = RegistrationJudge.objects.create(registration=registration, judge=judge)
        else:
            relation.judge = judge
            relation.save()
        relation.judge.schools.set([registration.school])
        saved_judge_ids.append(relation.pk)
    for reg_judge in list(registration.judges.all()):
        if reg_judge.pk not in saved_judge_ids:
            judge = reg_judge.judge
            reg_judge.delete()
            try:
                judge.delete()
            except Exception:
                pass
    return registration


@require_http_methods(["GET", "POST"])
def registration_portal(request, code=None):
    registration = None
    if code:
        registration = Registration.objects.select_related("school").filter(herokunator_code=code).first()
        if not registration:
            raise Http404()
    school_choices = build_school_choices()
    reg_form, team_formset, judge_formset = get_registration_forms(request, registration, school_choices)
    config = RegistrationConfig.get_active()
    if request.method == "POST":
        if reg_form.is_valid() and team_formset.is_valid() and judge_formset.is_valid():
            if config and not config.is_open():
                reg_form.add_error(None, "Registration is currently closed")
            else:
                try:
                    with transaction.atomic():
                        saved = save_registration(reg_form, team_formset, judge_formset, registration)
                except forms.ValidationError as error:
                    team_formset._non_form_errors = team_formset.error_class(error.messages)
                else:
                    return redirect(reverse("registration_portal_edit", args=[saved.herokunator_code]))
    summary = summarise_registration(registration) if registration else None
    context = {
        "registration_form": reg_form,
        "team_formset": team_formset,
        "judge_formset": judge_formset,
        "config": config,
        "summary": summary,
        "max_teams": MAX_TEAMS,
    }
    return render(request, "registration/portal.html", context)


@require_http_methods(["GET"])
def proxy_schools_active(request):
    """Proxy endpoint for active schools to avoid CORS issues."""
    try:
        response = requests.get(SCHOOL_ACTIVE_URL, timeout=10)
        if response.ok:
            return JsonResponse(response.json(), safe=False)
        return JsonResponse({"error": "Failed to fetch schools"}, status=response.status_code)
    except RequestException as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["GET"])
def proxy_schools_all(request):
    """Proxy endpoint for all schools to avoid CORS issues."""
    try:
        response = requests.get(SCHOOL_ALL_URL, timeout=10)
        if response.ok:
            return JsonResponse(response.json(), safe=False)
        return JsonResponse({"error": "Failed to fetch schools"}, status=response.status_code)
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
        return JsonResponse({"error": "Failed to fetch debaters"}, status=response.status_code)
    except RequestException as e:
        return JsonResponse({"error": str(e)}, status=500)
