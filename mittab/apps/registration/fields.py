"""Registration schedules always use Eastern local time in staff forms."""

from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone


REGISTRATION_TIME_ZONE = ZoneInfo("America/New_York")


class RegistrationDateTimeField(forms.DateTimeField):
    def to_python(self, value):
        # Keep Django's validation for ambiguous/nonexistent DST clock times.
        with timezone.override(REGISTRATION_TIME_ZONE):
            return super().to_python(value)

    def prepare_value(self, value):
        with timezone.override(REGISTRATION_TIME_ZONE):
            return super().prepare_value(value)
