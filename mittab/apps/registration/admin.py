from django.contrib import admin

from mittab.apps.tab.models import Judge, Team

from .models import Registration, RegistrationConfig, RegistrationContent


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


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("school", "email", "herokunator_code", "created_at")
    search_fields = ("school__name", "email", "herokunator_code")
    inlines = (RegistrationTeamInline, RegistrationJudgeInline)


@admin.register(RegistrationConfig)
class RegistrationConfigAdmin(admin.ModelAdmin):
    list_display = ("allow_new_registrations", "allow_registration_edits", "updated_at")


@admin.register(RegistrationContent)
class RegistrationContentAdmin(admin.ModelAdmin):
    list_display = ("updated_at",)
