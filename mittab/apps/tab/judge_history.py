"""Manual edits on pairing cards belong to the latest automatic assignment."""
from django.contrib.contenttypes.models import ContentType
from django.db.models import OuterRef, Subquery
from django.db.models.functions import Coalesce

from mittab.apps.tab.models import AuditEvent


def record_judge_pairing(pairings, model):
    """Keep a reset boundary even when automatic pairing repeats the same panel."""
    content_type = ContentType.objects.get_for_model(model)
    AuditEvent.objects.bulk_create([
        AuditEvent(content_type=content_type, object_id=pairing.pk,
                   object_repr=str(pairing), event_type=AuditEvent.JUDGES_PAIRED)
        for pairing in pairings
    ])


def current_manual_judge_events(model, pairing_ids):
    """Leave older edits in the audit trail, but omit them from current cards."""
    content_type = ContentType.objects.get_for_model(model)
    latest_pairing = AuditEvent.objects.filter(
        content_type=content_type, object_id=OuterRef("object_id"),
        event_type=AuditEvent.JUDGES_PAIRED,
    ).order_by("-id").values("id")[:1]
    return AuditEvent.objects.filter(
        content_type=content_type, object_id__in=pairing_ids,
        event_type__in=[AuditEvent.MANUAL_JUDGE_ASSIGN,
                        AuditEvent.MANUAL_JUDGE_REMOVE,
                        AuditEvent.MANUAL_CHAIR_CHANGE],
    ).filter(id__gt=Coalesce(Subquery(latest_pairing), 0)).select_related("user")
