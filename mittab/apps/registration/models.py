from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models

from mittab.apps.tab.models import School
from mittab.libs.haikunator import Haikunator


def validate_safe_url(value):
    """Restrict URLs to http(s) so users can't store javascript: or data: URIs."""
    URLValidator(schemes=["http", "https"])(value)


class RegistrationConfig(models.Model):
    SINGLETON_PK = 1

    allow_new_registrations = models.BooleanField(default=True)
    allow_registration_edits = models.BooleanField(default=True)
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

    def can_create(self):
        return self.allow_new_registrations

    def can_modify(self):
        return self.allow_registration_edits


class Registration(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE)
    email = models.EmailField()
    herokunator_code = models.CharField(max_length=255, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.herokunator_code:
            haikunator = Haikunator()
            code = haikunator.haikunate(token_length=0)
            while Registration.objects.filter(herokunator_code=code).exists():
                code = haikunator.haikunate(token_length=0)
            self.herokunator_code = code
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


class RegistrationLink(TournamentLinkBase):
    """Link surfaced after a successful registration and in the confirmation email."""
