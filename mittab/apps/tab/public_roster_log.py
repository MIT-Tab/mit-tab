"""Record only explicitly public roster changes, never private audit payloads."""

from contextlib import contextmanager
from contextvars import ContextVar

from django.db.models.signals import m2m_changed, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone

from mittab.apps.registration.models import RegistrationConfig
from mittab.apps.tab.models import (
    Debater,
    Judge,
    JudgeExpectedCheckIn,
    PublicRosterChange,
    PublicRosterLogState,
    School,
    TabSettings,
    Team,
)
from mittab.libs.cacheing.public_cache import (
    invalidate_public_judges_cache,
    invalidate_public_teams_cache,
)


IMPORT_IN_PROGRESS = ContextVar("public_roster_import_in_progress", default=False)


@contextmanager
def suppress_public_roster_logging():
    """Exclude an entire import, including nested model and relationship saves."""
    token = IMPORT_IN_PROGRESS.set(True)
    try:
        yield
    finally:
        IMPORT_IN_PROGRESS.reset(token)


def registration_closed():
    config = RegistrationConfig.get_active()
    return not config or bool(
        not config.allow_new_registrations
        or (
            config.new_registrations_close_at
            and timezone.now() >= config.new_registrations_close_at
        )
    )


def log_state():
    """Latch first publication; hiding a published list does not reset history."""
    state = PublicRosterLogState.objects.filter(pk=1).first()
    if state is None:
        if not TabSettings.get("teams_public", 0):
            return None
        state, _ = PublicRosterLogState.objects.get_or_create(
            pk=1,
            defaults={"first_published_at": timezone.now()},
        )
    if state.started_at is None and registration_closed():
        PublicRosterLogState.objects.filter(pk=1, started_at__isnull=True).update(
            started_at=timezone.now(),
        )
        state.refresh_from_db()
    return state


def logging_enabled():
    if IMPORT_IN_PROGRESS.get():
        return False
    state = log_state()
    return bool(state and state.started_at and registration_closed())


@receiver(post_save, sender=TabSettings)
@receiver(post_save, sender=RegistrationConfig)
def track_log_start(sender, instance, raw=False, **kwargs):
    if raw:
        return
    if sender == RegistrationConfig or instance.key == "teams_public":
        log_state()


@receiver(post_save, sender=PublicRosterChange)
@receiver(pre_delete, sender=PublicRosterChange)
def invalidate_roster_history(**kwargs):
    invalidate_public_teams_cache()
    invalidate_public_judges_cache()


def record(kind, message, involves_debaters=False):
    if logging_enabled():
        PublicRosterChange.objects.create(
            list_kind=kind,
            message=message,
            involves_debaters=involves_debaters,
        )
    if kind == "teams":
        invalidate_public_teams_cache()
    else:
        invalidate_public_judges_cache()


def affiliation(school_id):
    return (
        School.objects.filter(pk=school_id).values_list("name", flat=True).first()
        or "None"
    )


# Deliberately excludes team names from updates, plus seeds, novice status,
# check-ins, calibration, housing, codes, contact details, and attribution.
FIELDS = {
    Team: ("school_id", "hybrid_school_id"),
    Judge: ("name",),
    Debater: ("name", "school_id"),
    School: ("name",),
    JudgeExpectedCheckIn: ("judge_id", "round_number"),
}


@receiver(pre_save, sender=Team)
@receiver(pre_save, sender=Judge)
@receiver(pre_save, sender=Debater)
@receiver(pre_save, sender=School)
@receiver(pre_save, sender=JudgeExpectedCheckIn)
def before_roster_save(sender, instance, raw=False, **kwargs):
    if raw or sender not in FIELDS or not logging_enabled():
        return
    instance.public_roster_before = (
        sender.objects.filter(pk=instance.pk).values(*FIELDS[sender]).first()
        if instance.pk
        else None
    )


@receiver(post_save, sender=Team)
@receiver(post_save, sender=Judge)
@receiver(post_save, sender=Debater)
@receiver(post_save, sender=School)
@receiver(post_save, sender=JudgeExpectedCheckIn)
def after_roster_save(
    sender, instance, created, raw=False, update_fields=None, **kwargs
):
    if raw or sender not in FIELDS:
        return
    before = getattr(instance, "public_roster_before", None)
    if before and update_fields:
        # Ignore in-memory changes to fields that this save did not persist.
        before = {
            field: (
                old
                if field in update_fields or field.removesuffix("_id") in update_fields
                else getattr(instance, field)
            )
            for field, old in before.items()
        }
    if sender in (Team, Judge):
        kind = "teams" if sender == Team else "judges"
        if created:
            record(
                kind,
                f'Added {"team" if sender == Team else "judge"} “{instance.name}”.',
            )
        elif before:
            for field in FIELDS[sender]:
                old, new = before[field], getattr(instance, field)
                if old != new:
                    label = "name" if field == "name" else "affiliation"
                    old, new = (
                        (old, new)
                        if field == "name"
                        else (
                            affiliation(old),
                            affiliation(new),
                        )
                    )
                    record(
                        kind, f"Changed {label} for “{instance.name}”: {old} → {new}."
                    )
        # Even changes that aren't logged can affect the list (e.g. seeds).
        if sender == Team:
            invalidate_public_teams_cache()
        else:
            invalidate_public_judges_cache()
    elif sender == Debater and before:
        for field in FIELDS[sender]:
            old, new = before[field], getattr(instance, field)
            if old != new and instance.team_set.exists():
                label = "name" if field == "name" else "affiliation"
                old, new = (
                    (old, new)
                    if field == "name"
                    else (affiliation(old), affiliation(new))
                )
                record(
                    "teams",
                    f"Changed debater {label} for “{instance.name}”: {old} → {new}.",
                    True,
                )
    elif sender == School and before and before["name"] != instance.name:
        if (
            Team.objects.filter(school=instance).exists()
            or Team.objects.filter(hybrid_school=instance).exists()
        ):
            record(
                "teams",
                f'Changed affiliation name: {before["name"]} → {instance.name}.',
            )
        if Debater.objects.filter(school=instance, team__isnull=False).exists():
            record(
                "teams",
                f'Changed debater affiliation name: {before["name"]} → {instance.name}.',
                True,
            )
        if instance.judge_set.exists():
            record(
                "judges",
                f'Changed affiliation name: {before["name"]} → {instance.name}.',
            )
    elif sender == JudgeExpectedCheckIn:
        if before and (before["judge_id"], before["round_number"]) != (
            instance.judge_id,
            instance.round_number,
        ):
            availability_change(before["judge_id"], before["round_number"], False)
        if created or (
            before
            and before != {field: getattr(instance, field) for field in FIELDS[sender]}
        ):
            availability_change(instance.judge_id, instance.round_number, True)
    if sender in (Debater, School):
        invalidate_public_teams_cache()
    if sender == School:
        invalidate_public_judges_cache()


def availability_change(judge_id, round_number, available):
    name = Judge.objects.filter(pk=judge_id).values_list("name", flat=True).first()
    if name:
        label = f"Round {round_number}" if round_number else "Outrounds"
        status = "available" if available else "unavailable"
        record(
            "judges",
            f"Changed availability for “{name}”: {label} {status}.",
        )


@receiver(pre_delete, sender=Team)
@receiver(pre_delete, sender=Judge)
@receiver(pre_delete, sender=Debater)
@receiver(pre_delete, sender=School)
@receiver(pre_delete, sender=JudgeExpectedCheckIn)
def before_roster_delete(sender, instance, origin=None, **kwargs):
    if sender in (Team, Judge):
        record(
            "teams" if sender == Team else "judges",
            f'Removed {"team" if sender == Team else "judge"} “{instance.name}”.',
        )
    elif sender == JudgeExpectedCheckIn:
        # Cascading deletion of a judge is already represented by its removal.
        origin_model = getattr(origin, "model", type(origin))
        if origin_model == JudgeExpectedCheckIn:
            availability_change(instance.judge_id, instance.round_number, False)
    elif sender == Debater:
        for team in instance.team_set.all():
            record(
                "teams",
                f"Removed debater “{instance.name}” from team “{team.name}”.",
                True,
            )
    elif sender == School:
        for team in Team.objects.filter(hybrid_school=instance).exclude(
            school=instance
        ):
            record(
                "teams",
                f"Removed hybrid affiliation “{instance.name}” from team “{team.name}”.",
            )
        for judge in instance.judge_set.all():
            record(
                "judges",
                f"Removed affiliation “{instance.name}” from judge “{judge.name}”.",
            )
        for debater in instance.debater_set.filter(team__isnull=False).distinct():
            record(
                "teams",
                f"Removed affiliation “{instance.name}” from debater “{debater.name}”.",
                True,
            )


@receiver(m2m_changed, sender=Team.debaters.through)
@receiver(m2m_changed, sender=Judge.schools.through)
def roster_membership_change(sender, instance, action, reverse, pk_set, **kwargs):
    if action == "pre_clear":
        if sender == Team.debaters.through:
            related = instance.team_set if reverse else instance.debaters
        else:
            related = instance.judge_set if reverse else instance.schools
        instance.public_cleared_ids = set(related.values_list("pk", flat=True))
        return
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    ids = (
        pk_set
        if action != "post_clear"
        else getattr(instance, "public_cleared_ids", set())
    )
    added = action == "post_add"
    verb = "Added" if added else "Removed"
    for related_id in sorted(ids or ()):
        if sender == Team.debaters.through:
            team = Team.objects.get(pk=related_id) if reverse else instance
            debater = instance if reverse else Debater.objects.get(pk=related_id)
            preposition = "on" if added else "from"
            record(
                "teams",
                f"{verb} debater “{debater.name}” {preposition} team “{team.name}”.",
                True,
            )
        else:
            judge = Judge.objects.get(pk=related_id) if reverse else instance
            school = instance if reverse else School.objects.get(pk=related_id)
            preposition = "for" if added else "from"
            record(
                "judges",
                f"{verb} affiliation “{school.name}” {preposition} judge “{judge.name}”.",
            )


def public_changes(kind):
    changes = PublicRosterChange.objects.filter(list_kind=kind)
    if kind == "teams":
        if not TabSettings.get("debaters_public", 1):
            changes = changes.filter(involves_debaters=False)
    # Project the public schema; never send AuditEvent or unrestricted entities.
    return changes.only("message", "created_at").order_by("-created_at", "-pk")
