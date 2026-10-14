from datetime import datetime, timedelta, timezone as datetime_timezone

from django.utils import timezone

from mittab.apps.tab.models import TabSettings


EST = datetime_timezone(timedelta(hours=-5), "EST")
WRITTEN_RFD_DEADLINE_FORMATS = (
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M",
)


def written_rfd_first_round():
    return int(TabSettings.get("written_rfd_first_round", -1) or -1)


def round_allows_written_rfd(round_obj):
    first_round = written_rfd_first_round()
    return first_round >= 1 and round_obj.round_number >= first_round


def written_rfd_deadline():
    raw_value = TabSettings.get("written_rfd_deadline", "") or ""
    if not isinstance(raw_value, str):
        return None

    raw_value = raw_value.strip()
    if not raw_value:
        return None

    for date_format in WRITTEN_RFD_DEADLINE_FORMATS:
        try:
            parsed = datetime.strptime(raw_value, date_format)
            return timezone.make_aware(parsed, EST)
        except ValueError:
            continue

    return None


def written_rfd_deadline_display(deadline=None):
    deadline = deadline if deadline is not None else written_rfd_deadline()
    if not deadline:
        return ""

    local_deadline = timezone.localtime(deadline, EST)
    return local_deadline.strftime("%b %d, %Y at %I:%M %p EST")


def written_rfd_editing_open(round_obj, now=None):
    if not round_allows_written_rfd(round_obj):
        return False

    deadline = written_rfd_deadline()
    if deadline is None:
        return False

    now = now or timezone.now()
    return now <= deadline
