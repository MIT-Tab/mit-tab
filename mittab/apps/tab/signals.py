from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from mittab.apps.tab.models import ManualJudgeAssignment, Round


@receiver(m2m_changed, sender=Round.judges.through)
def track_manual_judge_assignments(instance, action, reverse, pk_set, **kwargs):
    if action not in {"post_add", "post_remove", "post_clear"}:
        return

    pk_set = pk_set or set()

    if action == "post_add":
        if not pk_set:
            return
        if reverse:
            judge = instance
            assignments = [
                ManualJudgeAssignment(round_id=round_id, judge=judge)
                for round_id in pk_set
            ]
        else:
            round_obj = instance
            assignments = [
                ManualJudgeAssignment(round=round_obj, judge_id=judge_id)
                for judge_id in pk_set
            ]
        ManualJudgeAssignment.objects.bulk_create(assignments, ignore_conflicts=True)

    elif action == "post_remove":
        if not pk_set:
            return
        if reverse:
            judge = instance
            ManualJudgeAssignment.objects.filter(
                judge=judge,
                round_id__in=pk_set,
            ).delete()
        else:
            round_obj = instance
            ManualJudgeAssignment.objects.filter(
                round=round_obj,
                judge_id__in=pk_set,
            ).delete()

    elif action == "post_clear":
        if reverse:
            judge = instance
            ManualJudgeAssignment.objects.filter(judge=judge).delete()
        else:
            round_obj = instance
            ManualJudgeAssignment.objects.filter(round=round_obj).delete()
