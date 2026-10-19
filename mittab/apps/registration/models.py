from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils import timezone

from mittab.apps.tab.models import EmailContentLog, School
from mittab.libs.codes import generate_readable_auth_code


def validate_safe_url(value):
    """Restrict URLs to http(s) so users can't store javascript: or data: URIs."""
    URLValidator(schemes=["http", "https"])(value)


class RegistrationConfig(models.Model):
    SINGLETON_PK = 1

    allow_new_registrations = models.BooleanField(default=True)
    allow_registration_edits = models.BooleanField(default=True)
    new_registrations_open_at = models.DateTimeField(null=True, blank=True)
    new_registrations_close_at = models.DateTimeField(null=True, blank=True)
    registration_edits_open_at = models.DateTimeField(null=True, blank=True)
    registration_edits_close_at = models.DateTimeField(null=True, blank=True)
    crash_housing_enabled = models.BooleanField(default=False)
    team_name_changes_allowed = models.BooleanField(default=False)
    disc_scratches_open = models.BooleanField(default=False)
    disc_scratch_quantity = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(pk=cls.SINGLETON_PK).first()

    @classmethod
    def get_or_create_active(cls):
        config, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return config

    def save(
        self,
        force_insert=False,
        force_update=False,
        using=None,
        update_fields=None,
    ):
        self.pk = self.SINGLETON_PK
        super().save(False, force_update, using, update_fields)

    @staticmethod
    def _within_window(enabled, opens_at, closes_at, now=None):
        if not enabled:
            return False
        now = now or timezone.now()
        if opens_at and now < opens_at:
            return False
        if closes_at and now >= closes_at:
            return False
        return True

    def can_create(self, now=None):
        return self._within_window(
            self.allow_new_registrations,
            self.new_registrations_open_at,
            self.new_registrations_close_at,
            now,
        )

    def can_modify(self, now=None):
        return self._within_window(
            self.allow_registration_edits,
            self.registration_edits_open_at,
            self.registration_edits_close_at,
            now,
        )


class Registration(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE)
    email = models.EmailField()
    dietary_requests = models.TextField(blank=True)
    housing_requests = models.TextField(blank=True)
    herokunator_code = models.CharField(max_length=255, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.school.name} — {self.email}"

    def save(self, *args, **kwargs):
        if not self.herokunator_code:
            self.herokunator_code = generate_readable_auth_code(
                lambda code: Registration.objects.filter(
                    herokunator_code=code,
                ).exists()
            )
        super().save(*args, **kwargs)


class RegistrationChangeLog(models.Model):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ACTION_CHOICES = (
        (CREATED, "Created"),
        (UPDATED, "Updated"),
        (DELETED, "Deleted"),
    )

    registration = models.ForeignKey(
        Registration,
        null=True,
        blank=True,
        related_name="change_logs",
        on_delete=models.SET_NULL,
    )
    registration_code = models.CharField(max_length=255, blank=True)
    school_name = models.CharField(max_length=50, blank=True)
    email = models.EmailField(max_length=254, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    summary = models.TextField(blank=True)
    changes = models.JSONField(default=dict, blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()} {self.registration_code}"


class RegistrationConfirmationEmailLog(EmailContentLog):
    registration = models.ForeignKey(
        Registration,
        related_name="confirmation_email_logs",
        on_delete=models.CASCADE,
    )
    email = models.EmailField(max_length=254)
    successful = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["registration", "sent_at"]),
            models.Index(fields=["email", "sent_at"]),
        ]
        ordering = ["-sent_at"]

    def __str__(self):
        status = "sent" if self.successful else "failed"
        return f"Registration confirmation {status}: {self.email}"


class TournamentLinkBase(models.Model):
    title = models.CharField(max_length=80)
    url = models.CharField(max_length=500, validators=[validate_safe_url])
    description = models.CharField(max_length=160, blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["display_order", "created_at"]

    def clean(self):
        super().clean()
        if self.url:
            try:
                validate_safe_url(self.url)
            except ValidationError as exc:
                raise ValidationError({"url": exc.messages}) from exc

    def __str__(self):
        return self.title


class InfoLink(TournamentLinkBase):
    """Tournament-wide link shown on the public homepage."""


class InfoText(models.Model):
    """Tournament-wide text shown with external information on the homepage."""

    DEFAULT_TITLE = "GA"
    DEFAULT_TEXT = "TBD"

    title = models.CharField(max_length=80)
    text = models.CharField(max_length=500)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.title

    @classmethod
    def ensure_defaults(cls):
        """Create the information every newly initialized tournament starts with."""
        return cls.objects.get_or_create(
            title=cls.DEFAULT_TITLE,
            defaults={"text": cls.DEFAULT_TEXT},
        )


class RegistrationLink(TournamentLinkBase):
    """Link surfaced after a successful registration and in the confirmation email."""
