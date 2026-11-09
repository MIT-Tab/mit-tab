from django import forms
from django.contrib import admin
from django.utils import timezone

from mittab.apps.registration.fields import (
    REGISTRATION_TIME_ZONE,
    RegistrationDateTimeField,
)
from mittab.apps.tab.models import Judge, Team

from .models import (
    InfoLink,
    InfoText,
    Registration,
    RegistrationChangeLog,
    RegistrationConfirmationEmailLog,
    RegistrationConfig,
    RegistrationLink,
)


class RegistrationTeamInline(admin.TabularInline):
    model = Team
    fk_name = "registration"
    extra = 0
    fields = ("name", "seed", "school", "hybrid_school")


class RegistrationJudgeInline(admin.TabularInline):
    model = Judge
    fk_name = "registration"
    extra = 0
    fields = ("name", "reported_experience", "rank", "email")


class RegistrationChangeLogInline(admin.TabularInline):
    model = RegistrationChangeLog
    fk_name = "registration"
    extra = 0
    can_delete = False
    readonly_fields = (
        "created_at",
        "action",
        "summary",
        "registration_code",
        "school_name",
        "email",
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


class RegistrationConfirmationEmailLogInline(admin.TabularInline):
    model = RegistrationConfirmationEmailLog
    fk_name = "registration"
    extra = 0
    can_delete = False
    readonly_fields = (
        "sent_at",
        "email",
        "successful",
        "error_message",
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("school", "email", "herokunator_code", "created_at")
    search_fields = ("school__name", "email", "herokunator_code")
    inlines = (
        RegistrationTeamInline,
        RegistrationJudgeInline,
        RegistrationConfirmationEmailLogInline,
        RegistrationChangeLogInline,
    )


@admin.register(RegistrationConfig)
class RegistrationConfigAdmin(admin.ModelAdmin):
    list_display = (
        "allow_new_registrations",
        "new_registrations_open_eastern",
        "new_registrations_close_eastern",
        "allow_registration_edits",
        "registration_edits_open_eastern",
        "registration_edits_close_eastern",
        "crash_housing_enabled",
        "updated_at",
    )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in (
            "new_registrations_open_at", "new_registrations_close_at",
            "registration_edits_open_at", "registration_edits_close_at",
        ):
            kwargs.update(
                form_class=RegistrationDateTimeField,
                widget=forms.DateTimeInput(
                    format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"},
                ),
                help_text="Eastern time (EST/EDT).",
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @staticmethod
    def _eastern_time(value):
        if value is None:
            return None
        return timezone.localtime(value, REGISTRATION_TIME_ZONE).strftime(
            "%Y-%m-%d %H:%M %Z",
        )

    @admin.display(description="New registrations open (Eastern)")
    def new_registrations_open_eastern(self, obj):
        return self._eastern_time(obj.new_registrations_open_at)

    @admin.display(description="New registrations close (Eastern)")
    def new_registrations_close_eastern(self, obj):
        return self._eastern_time(obj.new_registrations_close_at)

    @admin.display(description="Registration edits open (Eastern)")
    def registration_edits_open_eastern(self, obj):
        return self._eastern_time(obj.registration_edits_open_at)

    @admin.display(description="Registration edits close (Eastern)")
    def registration_edits_close_eastern(self, obj):
        return self._eastern_time(obj.registration_edits_close_at)

    def has_add_permission(self, request):
        return not RegistrationConfig.objects.exists()


@admin.register(RegistrationChangeLog)
class RegistrationChangeLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "action",
        "school_name",
        "email",
        "registration_code",
        "summary",
    )
    search_fields = ("school_name", "email", "registration_code", "summary")
    readonly_fields = (
        "registration",
        "registration_code",
        "school_name",
        "email",
        "action",
        "summary",
        "changes",
        "snapshot",
        "created_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(RegistrationConfirmationEmailLog)
class RegistrationConfirmationEmailLogAdmin(admin.ModelAdmin):
    list_display = ("sent_at", "registration", "email", "successful")
    list_filter = ("successful",)
    search_fields = ("registration__school__name", "email", "error_message")
    readonly_fields = (
        "registration",
        "email",
        "successful",
        "error_message",
        "sent_at",
    )

    def has_add_permission(self, request):
        return False


class _TournamentLinkAdmin(admin.ModelAdmin):
    list_display = ("title", "url", "is_active", "display_order", "updated_at")
    list_editable = ("is_active", "display_order")
    list_display_links = ("title",)
    search_fields = ("title", "url")
    ordering = ("display_order", "created_at")


@admin.register(InfoLink)
class InfoLinkAdmin(_TournamentLinkAdmin):
    pass


@admin.register(InfoText)
class InfoTextAdmin(admin.ModelAdmin):
    list_display = ("title", "text", "is_active", "display_order", "updated_at")
    list_editable = ("is_active", "display_order")
    list_display_links = ("title",)
    search_fields = ("title", "text")
    ordering = ("display_order", "created_at")


@admin.register(RegistrationLink)
class RegistrationLinkAdmin(_TournamentLinkAdmin):
    pass
