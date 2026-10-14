"""Structured, secret-free pairing evidence shared by staff and board views."""
from functools import wraps
import hashlib
import json

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from mittab.apps.tab.models import AuditEvent, Outround, Round


def pairing_snapshot(pairing):
    """Do not include judge rank, eligibility, scratch data, codes or free text."""
    data = {
        "gov_team": {"id": pairing.gov_team_id, "name": pairing.gov_team.display},
        "opp_team": {"id": pairing.opp_team_id, "name": pairing.opp_team.display},
        "room": ({"id": pairing.room_id, "name": pairing.room.name}
                 if pairing.room_id else None),
        "judges": list(pairing.judges.order_by("name", "id").values("id", "name")),
        "chair": pairing.chair_id,
        "victor": pairing.victor,
    }
    if isinstance(pairing, Outround):
        data.update(num_teams=pairing.num_teams, division=pairing.type_of_round,
                    choice=pairing.choice, sidelock=pairing.sidelock)
    else:
        data["round_number"] = pairing.round_number
        data["scores"] = [
            {"debater": row["debater_id"], "role": row["debater_role"],
             "name": row["debater__name"],
             "speaks": str(row["speaks"]), "ranks": str(row["ranks"])}
            for row in pairing.roundstats_set.order_by("debater_role", "id").values(
                "debater_id", "debater__name", "debater_role", "speaks", "ranks")
        ]
    return data


def record_pairing_change(pairing, before, actor, source):
    after = pairing_snapshot(pairing)
    if before != after:
        AuditEvent.record(
            pairing, AuditEvent.CREATE if before is None else AuditEvent.EDIT, actor,
            changes={"public": {"before": before, "after": after, "source": source}},
        )


def record_pairing_removal(model, object_id, before, actor):
    AuditEvent.objects.create(
        content_type=ContentType.objects.get_for_model(model), object_id=object_id,
        event_type=AuditEvent.EDIT, user=actor,
        object_repr=f"{model.__name__} {object_id}",
        changes={"public": {
            "before": before, "after": None, "source": "admin deletion",
        }},
    )


def pairing_revision(pairing):
    snapshot = pairing if isinstance(pairing, dict) else pairing_snapshot(pairing)
    return hashlib.sha256(
        json.dumps(
            snapshot,
            sort_keys=True).encode()).hexdigest()


def audit_pairing_change(model, source):
    """Capture legacy staff writes, including ballot entry and manual corrections."""
    def decorate(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            round_id = args[0] if args else kwargs.get(
                "round_id", kwargs.get("outround_id"))
            round_model = model or (Outround if kwargs.get(
                "outround") or kwargs.get("is_outround") else Round)
            with transaction.atomic():
                pairing = round_model.objects.select_for_update().filter(
                    pk=round_id).first()
                if pairing is None:
                    return view(request, *args, **kwargs)
                before = pairing_snapshot(pairing)
                response = view(request, *args, **kwargs)
                pairing.refresh_from_db()
                record_pairing_change(pairing, before, request.user, source)
                return response
        return wrapper
    return decorate


def audit_pairing_generation(model, source):
    """Record staff generation/bulk assignment, including deleted pairing evidence."""
    def decorate(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method != "POST":
                return view(request, *args, **kwargs)
            before = {pairing.pk: (pairing, pairing_snapshot(pairing))
                      for pairing in model.objects.all()}
            response = view(request, *args, **kwargs)
            remaining = set()
            for pairing in model.objects.all():
                remaining.add(pairing.pk)
                record_pairing_change(pairing, before.get(pairing.pk, (None, None))[1],
                                      request.user, source)
            for object_id in set(before) - remaining:
                pairing, snapshot = before[object_id]
                AuditEvent.record(pairing, AuditEvent.EDIT, request.user, changes={
                    "public": {"before": snapshot, "after": None, "source": source},
                })
            return response
        return wrapper
    return decorate


PUBLIC_SNAPSHOT_KEYS = {
    "gov_team", "opp_team", "room", "judges", "chair", "victor", "num_teams",
    "division", "choice", "sidelock", "round_number", "scores",
}
PUBLIC_SOURCES = {
    "board pairing",
    "board correction",
    "board team swap",
    "staff pairing",
    "staff re-pairing",
    "staff judge assignment",
    "staff room assignment",
    "staff room correction",
    "staff team correction",
    "staff side correction",
    "ballot entry",
    "panel ballot entry",
    "staff outround pairing",
    "staff break",
    "staff outround result",
    "staff choice correction",
    "admin correction",
    "admin scores",
    "admin deletion",
}


def _safe_identity(value):
    if (isinstance(value, dict) and isinstance(value.get("id"), int)
            and isinstance(value.get("name"), str)):
        return {"id": value["id"], "name": value["name"]}
    return None


def safe_snapshot(value):
    """Reconstruct nested fields; never forward arbitrary audit JSON to a template."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in PUBLIC_SNAPSHOT_KEYS:
        item = value.get(key)
        if key in {"gov_team", "opp_team", "room"}:
            result[key] = _safe_identity(item)
        elif key == "judges":
            result[key] = [_safe_identity(judge) for judge in item or []
                           if _safe_identity(judge) is not None]
        elif key == "scores":
            result[key] = [{field: (score.get(field) if isinstance(
                score.get(field), (str, int, float)) else None) for field in
                            ("debater", "name", "role", "speaks", "ranks")}
                           for score in item or [] if isinstance(score, dict)]
        elif key in value and isinstance(item, (int, bool)):
            result[key] = item
    return result


def _history_value(field, value):
    if value is None or value == "":
        return "Unassigned"
    if isinstance(value, dict):
        return value.get("name", "Unassigned")
    if isinstance(value, list):
        if field == "scores":
            return "; ".join(
                f"{score['name']} ({str(score['role'] or '').upper()}): "
                f"{score['speaks']} speaks, {score['ranks']} rank"
                for score in value)
        return ", ".join(str(judge.get("name", "")) for judge in value)
    if field == "victor":
        return dict(Round.VICTOR_CHOICES).get(value, "Unknown")
    if field == "choice":
        return dict(Outround.CHOICES).get(value, "Unknown")
    return str(value)


def pairing_history(model, object_id):
    events = AuditEvent.objects.filter(
        content_type=ContentType.objects.get_for_model(model), object_id=object_id,
    ).select_related("user").order_by("created_at", "id")
    history = []
    manual_fields = {
        AuditEvent.MANUAL_JUDGE_ASSIGN: ("assigned_judge", "removed_judge"),
        AuditEvent.MANUAL_JUDGE_REMOVE: ("removed_judge",),
        AuditEvent.MANUAL_CHAIR_CHANGE: ("chair", "new_chair", "old_chair"),
    }
    for event in events:
        if not isinstance(event.changes, dict):
            continue
        public = event.changes.get("public")
        changes = []
        if isinstance(public, dict):
            before = safe_snapshot(public.get("before"))
            after = safe_snapshot(public.get("after"))
            changes = [{"field": key.replace("_", " "),
                        "before": _history_value(key, before.get(key)),
                        "after": _history_value(key, after.get(key))}
                       for key in sorted(set(before) | set(after))
                       if before.get(key) != after.get(key)]
        elif event.event_type in manual_fields:
            changes = [{"field": field.replace("_", " "), "before": "",
                        "after": event.changes[field]}
                       for field in manual_fields[event.event_type]
                       if field in event.changes
                       and isinstance(event.changes[field], str)]
        if changes:
            source = public.get("source") if isinstance(public, dict) else ""
            source = source if source in PUBLIC_SOURCES else ""
            history.append({"time": event.created_at,
                            "actor": event.user_display,
                            "label": dict(AuditEvent.EVENT_CHOICES).get(
                                event.event_type, "Changed"),
                            "changes": changes,
                            "source": source})
    return history


def deleted_pairings(model, field, value):
    """Keep removed matchups reviewable without exposing raw audit records."""
    rows = []
    events = AuditEvent.objects.filter(
        content_type=ContentType.objects.get_for_model(model),
    ).order_by("created_at", "id")
    for event in events:
        if not isinstance(event.changes, dict):
            continue
        public = event.changes.get("public")
        if not isinstance(public, dict) or public.get("after") is not None:
            continue
        before = safe_snapshot(public.get("before"))
        if before.get(field) != value:
            continue
        if model.objects.filter(pk=event.object_id).exists():
            continue
        rows.append(dict(before, id=event.object_id,
                         outcome=dict(model.VICTOR_CHOICES).get(before.get("victor")),
                         history=pairing_history(model, event.object_id)))
    return rows
