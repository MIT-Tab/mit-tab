from django.contrib import admin

from mittab.apps.tab.models import Judge, Team

from .models import (
    InfoLink,
    Registration,
    RegistrationChangeLog,
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
    fields = ("name", "rank", "email")


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


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("school", "email", "herokunator_code", "created_at")
    search_fields = ("school__name", "email", "herokunator_code")
    inlines = (
        RegistrationTeamInline,
        RegistrationJudgeInline,
        RegistrationChangeLogInline,
    )


@admin.register(RegistrationConfig)
class RegistrationConfigAdmin(admin.ModelAdmin):
    list_display = ("allow_new_registrations", "allow_registration_edits", "updated_at")

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


class _TournamentLinkAdmin(admin.ModelAdmin):
    list_display = ("title", "url", "is_active", "display_order", "updated_at")
    list_editable = ("is_active", "display_order")
    list_display_links = ("title",)
    search_fields = ("title", "url")
    ordering = ("display_order", "created_at")


@admin.register(InfoLink)
class InfoLinkAdmin(_TournamentLinkAdmin):
    pass


@admin.register(RegistrationLink)
class RegistrationLinkAdmin(_TournamentLinkAdmin):
    pass
