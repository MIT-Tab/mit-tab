from django.contrib import admin

from .models import (
    RegistrationConfig,
    Registration,
    RegistrationJudge,
    RegistrationTeam,
    RegistrationTeamMember,
)


class RegistrationTeamInline(admin.TabularInline):
    model = RegistrationTeam
    extra = 0


class RegistrationJudgeInline(admin.TabularInline):
    model = RegistrationJudge
    extra = 0


class RegistrationTeamMemberInline(admin.TabularInline):
    model = RegistrationTeamMember
    extra = 0


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("school", "email", "herokunator_code", "created_at")
    search_fields = ("school__name", "email", "herokunator_code")
    inlines = (RegistrationTeamInline, RegistrationJudgeInline)


@admin.register(RegistrationConfig)
class RegistrationConfigAdmin(admin.ModelAdmin):
    list_display = ("registration_open", "registration_close", "tournament_start")


@admin.register(RegistrationTeam)
class RegistrationTeamAdmin(admin.ModelAdmin):
    list_display = ("registration", "team", "is_free_seed")
    inlines = (RegistrationTeamMemberInline,)
