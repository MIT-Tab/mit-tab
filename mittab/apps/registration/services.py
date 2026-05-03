from django import forms
from django.db import IntegrityError
from django.db.models import Prefetch
import requests
from requests.exceptions import RequestException

from mittab.apps.tab.models import CheckIn, Debater, Judge, School, TabSettings, Team

from .models import Registration, RegistrationChangeLog

SCHOOL_ACTIVE_URL = "https://results.apda.online/api/schools/all/"


def fetch_remote_schools():
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
        CheckIn.objects.filter(judge=judge, round_number__in=removals).delete()


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
        "email": "",
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
        apda_id = selection["apda_id"]
        school = School.objects.filter(apda_id=apda_id).first()
        if school:
            cache[key] = school
            return school
        name = selection.get("name", "").strip()
        if name:
            school = School.objects.filter(name__iexact=name).first()
            if school:
                if school.apda_id in (None, -1):
                    school.apda_id = apda_id
                    school.save(update_fields=["apda_id"])
                cache[key] = school
                return school
        try:
            school, _ = School.objects.get_or_create(
                apda_id=apda_id,
                defaults={"name": name},
            )
        except IntegrityError as exc:
            school = School.objects.filter(name__iexact=name).first()
            if not school:
                raise forms.ValidationError(
                    "School already exists, select it instead"
                ) from exc
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
    except IntegrityError as exc:
        existing = School.objects.filter(name__iexact=name).first()
        if existing:
            school = existing
        else:
            raise forms.ValidationError(
                "School already exists, select it instead"
            ) from exc
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


def _existing_registration_debater(registration, debater_id):
    if not registration or not registration.pk or not debater_id:
        return None
    return (
        Debater.objects.filter(pk=debater_id, team__registration=registration)
        .distinct()
        .first()
    )


def get_or_create_debater(data, school, registration=None):
    name = (data["name"] or "").strip()
    if not name:
        raise forms.ValidationError("Debater name required")
    debater = _existing_registration_debater(registration, data.get("id")) or Debater()
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


def _school_snapshot(school):
    if not school:
        return None
    return {
        "id": school.pk,
        "name": school.name,
        "apda_id": school.apda_id,
    }


def _debater_snapshot(debater):
    return {
        "id": debater.pk,
        "name": debater.name,
        "apda_id": debater.apda_id,
        "novice_status": debater.novice_status,
        "qualified": debater.qualified,
        "school": _school_snapshot(debater.school),
    }


def registration_snapshot(registration):
    if not registration:
        return {}
    registration = (
        Registration.objects.select_related("school")
        .prefetch_related(
            Prefetch(
                "teams",
                queryset=Team.objects.select_related(
                    "school", "hybrid_school"
                ).prefetch_related("debaters__school"),
            ),
            Prefetch(
                "judges",
                queryset=Judge.objects.prefetch_related("schools", "checkin_set"),
            ),
        )
        .get(pk=registration.pk)
    )
    teams = []
    for team in registration.teams.all():
        teams.append(
            {
                "id": team.pk,
                "name": team.name,
                "seed": team.seed,
                "school": _school_snapshot(team.school),
                "hybrid_school": _school_snapshot(team.hybrid_school),
                "debaters": [
                    _debater_snapshot(debater) for debater in team.debaters.all()
                ],
            }
        )
    judges = []
    for judge in registration.judges.all():
        judges.append(
            {
                "id": judge.pk,
                "name": judge.name,
                "experience": str(judge.rank),
                "schools": [_school_snapshot(school) for school in judge.schools.all()],
                "availability_rounds": sorted(
                    checkin.round_number for checkin in judge.checkin_set.all()
                ),
            }
        )
    return {
        "id": registration.pk,
        "code": registration.herokunator_code,
        "school": _school_snapshot(registration.school),
        "email": registration.email,
        "teams": teams,
        "judges": judges,
    }


def _label(value):
    if isinstance(value, dict):
        return value.get("name") or value.get("code") or value.get("id")
    return value


def _summarize_collection(before_items, after_items, label):
    changes = {}
    summary = []
    before_map = {item["id"]: item for item in before_items}
    after_map = {item["id"]: item for item in after_items}
    before_ids = set(before_map)
    after_ids = set(after_map)
    added = [after_map[item_id] for item_id in sorted(after_ids - before_ids)]
    removed = [before_map[item_id] for item_id in sorted(before_ids - after_ids)]
    updated = []
    for item_id in sorted(before_ids & after_ids):
        if before_map[item_id] != after_map[item_id]:
            updated.append(
                {
                    "before": before_map[item_id],
                    "after": after_map[item_id],
                }
            )
    if added:
        changes["added"] = added
        summary.append(
            f"added {len(added)} {label}{'' if len(added) == 1 else 's'}"
        )
    if removed:
        changes["removed"] = removed
        summary.append(
            f"removed {len(removed)} {label}{'' if len(removed) == 1 else 's'}"
        )
    if updated:
        changes["updated"] = updated
        summary.append(
            f"updated {len(updated)} {label}{'' if len(updated) == 1 else 's'}"
        )
    return changes, summary


def diff_registration_snapshots(before, after):
    if not before:
        return {
            "created": after,
        }, ["created registration"]
    changes = {}
    summary = []
    for field in ("school", "email"):
        if before.get(field) != after.get(field):
            changes[field] = {
                "before": before.get(field),
                "after": after.get(field),
            }
            summary.append(f"changed {field} to {_label(after.get(field))}")
    team_changes, team_summary = _summarize_collection(
        before.get("teams", []), after.get("teams", []), "team"
    )
    judge_changes, judge_summary = _summarize_collection(
        before.get("judges", []), after.get("judges", []), "judge"
    )
    if team_changes:
        changes["teams"] = team_changes
        summary.extend(team_summary)
    if judge_changes:
        changes["judges"] = judge_changes
        summary.extend(judge_summary)
    return changes, summary


def log_registration_change(registration, action, before=None, after=None):
    snapshot = after or before or registration_snapshot(registration)
    changes, summary_parts = diff_registration_snapshots(before or {}, snapshot)
    if action == RegistrationChangeLog.UPDATED and not changes:
        return None
    if action == RegistrationChangeLog.DELETED:
        changes = {"deleted": before or snapshot}
        summary_parts = ["deleted registration"]
    school = snapshot.get("school") or {}
    return RegistrationChangeLog.objects.create(
        registration=registration if action != RegistrationChangeLog.DELETED else None,
        registration_code=snapshot.get("code", ""),
        school_name=school.get("name", ""),
        email=snapshot.get("email", ""),
        action=action,
        summary=", ".join(summary_parts),
        changes=changes,
        snapshot=snapshot,
    )


def save_registration(reg_form, team_formset, judge_formset, registration):
    before = (
        registration_snapshot(registration) if registration and registration.pk else {}
    )
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
        member_schools = [
            resolve_school(member["school"], cache=school_cache) for member in members
        ]
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
            get_or_create_debater(member, member_schools[index], registration)
            for index, member in enumerate(members)
        ]
        team.debaters.set(debaters)
        saved_team_ids.append(team.pk)
    for team in registration.teams.exclude(pk__in=saved_team_ids):
        team.debaters.clear()
        team.delete()

    saved_judge_ids = []
    registration_judge_emails = []
    for form in judge_formset:
        if form.cleaned_data.get("DELETE"):
            continue
        payload = form.get_payload()
        judge = Judge.objects.filter(
            pk=payload.get("judge_id"), registration=registration
        ).first() or Judge(registration=registration)
        judge.name = uniquify_name(Judge, payload["name"], exclude_pk=judge.pk)
        judge.rank = payload["experience"]
        judge.email = None
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
        registration_judge_emails.append({
            "judge_id": judge.pk,
            "email": payload["email"],
        })
    for judge in registration.judges.exclude(pk__in=saved_judge_ids):
        judge.delete()

    after = registration_snapshot(registration)
    action = RegistrationChangeLog.UPDATED if before else RegistrationChangeLog.CREATED
    log_registration_change(registration, action, before=before, after=after)
    registration.transient_judge_emails = registration_judge_emails
    return registration


def log_registration_deleted(registration):
    snapshot = registration_snapshot(registration)
    return log_registration_change(
        registration,
        RegistrationChangeLog.DELETED,
        before=snapshot,
        after=snapshot,
    )
