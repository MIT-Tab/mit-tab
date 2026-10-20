from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

from mittab.apps.tab.models import Round, TabSettings
from mittab.apps.tab.public_rankings import get_standings_publication_setting


def final_inround_completed_at():
    """Return when the last ballot in the final inround was first completed."""
    try:
        final_round = int(TabSettings.get("tot_rounds", 0) or 0)
    except (TypeError, ValueError):
        return None
    if final_round < 1:
        return None

    rounds = Round.objects.filter(round_number=final_round)
    if not rounds.exists() or rounds.filter(victor=Round.UNKNOWN).exists():
        return None
    if rounds.filter(result_entered_at__isnull=True).exists():
        return None
    return rounds.aggregate(completed_at=Max("result_entered_at"))["completed_at"]


def speaker_results_available(now=None):
    """Expose speakers after outround publication or a three-hour embargo."""
    if get_standings_publication_setting("team_results")["published"]:
        return True
    completed_at = final_inround_completed_at()
    if completed_at is None:
        return False
    return (now or timezone.now()) >= completed_at + timedelta(hours=3)

