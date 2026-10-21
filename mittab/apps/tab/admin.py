from django.contrib import admin

from django import forms

from mittab.apps.tab import models
from mittab.libs.outround_tab_logic.pairing import prepare_challenge_edits


class RoundAdminForm(forms.ModelForm):
    chair = forms.ModelChoiceField(queryset=models.Judge.objects.all())

    class Meta:
        model = models.Round
        fields = "__all__"


class OutroundAdminForm(forms.ModelForm):
    chair = forms.ModelChoiceField(queryset=models.Judge.objects.all())

    class Meta:
        model = models.Outround
        fields = "__all__"


class RoundAdmin(admin.ModelAdmin):
    form = RoundAdminForm
    filter_horizontal = ("judges", )


class OutroundAdmin(admin.ModelAdmin):
    form = OutroundAdminForm
    filter_horizontal = ("judges", )


class AttributionAdminMixin:
    readonly_fields = ("created_by", "created_at")

    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, "created_by") and not obj.created_by_id:
            obj.created_by = request.user
        super(AttributionAdminMixin, self).save_model(request, obj, form, change)
        event_type = models.AuditEvent.EDIT if change else models.AuditEvent.CREATE
        changes = {"fields": list(form.changed_data)} if change else {}
        models.AuditEvent.record(obj, event_type, request.user, changes=changes)


class TeamAdmin(AttributionAdminMixin, admin.ModelAdmin):
    exclude = ("team_code",)
    filter_horizontal = ("debaters", )


class JudgeAdmin(AttributionAdminMixin, admin.ModelAdmin):
    filter_horizontal = ("schools", "required_room_tags")


class ScratchAdmin(AttributionAdminMixin, admin.ModelAdmin):
    pass


class ManualJudgeAssignmentAdmin(admin.ModelAdmin):
    readonly_fields = ("round", "judge", "created_by", "created_at")
    list_display = ("round", "judge", "created_by", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class AuditEventAdmin(admin.ModelAdmin):
    readonly_fields = (
        "content_type",
        "object_id",
        "event_type",
        "user",
        "created_at",
        "object_repr",
        "changes",
        "note",
    )
    list_display = ("created_at", "event_type", "object_repr", "user")
    list_filter = ("event_type", "content_type")
    search_fields = ("object_repr", "note")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MotionAdmin(admin.ModelAdmin):
    list_display = ("round_display", "motion_text_truncated", "is_published", "updated_at")
    list_filter = ("is_published", "outround_type")
    search_fields = ("motion_text", "info_slide")
    ordering = ("round_number", "outround_type", "-num_teams")

    def motion_text_truncated(self, obj):
        return obj.motion_text[:100] + "..." if len(obj.motion_text) > 100 else obj.motion_text
    motion_text_truncated.short_description = "Motion Text"


class ExperimentalAdmin(admin.ModelAdmin):
    setting_key = None

    def enabled(self):
        return models.TabSettings.get(self.setting_key, 0) in self.enabled_values

    enabled_values = (1,)

    def get_model_perms(self, request):
        return super().get_model_perms(request) if self.enabled() else {}

    def has_module_permission(self, request):
        return self.enabled() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self.enabled() and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return self.enabled() and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.enabled() and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.enabled() and super().has_delete_permission(request, obj)


class OutroundChallengeAdmin(ExperimentalAdmin):
    setting_key = "outround_pairing_mode"
    enabled_values = (1, 2)

    def save_model(self, request, obj, form, change):
        list(models.BreakingTeam.objects.select_for_update().filter(
            type_of_team=obj.type_of_round,
        ))
        prepare_challenge_edits(obj.type_of_round)
        super().save_model(request, obj, form, change)


admin.site.register(models.Debater)
admin.site.register(models.Team, TeamAdmin)
admin.site.register(models.School)
admin.site.register(models.Judge, JudgeAdmin)
admin.site.register(models.JudgeCodeEmailLog)
admin.site.register(models.WrittenRFDEmailLog)
admin.site.register(models.Scratch, ScratchAdmin)
admin.site.register(models.Round, RoundAdmin)
admin.site.register(models.RoundStats)
admin.site.register(models.CheckIn)
admin.site.register(models.RoomCheckIn)
admin.site.register(models.TabSettings)
admin.site.register(models.UserTournamentSetupPreference)
admin.site.register(models.Room)
admin.site.register(models.RoomTag)
admin.site.register(models.RankingGroup)
admin.site.register(models.Bye)
admin.site.register(models.NoShow)
admin.site.register(models.BreakingTeam)
admin.site.register(models.Outround, OutroundAdmin)
admin.site.register(models.OutroundChallenge, OutroundChallengeAdmin)
admin.site.register(models.Motion, MotionAdmin)
admin.site.register(models.ManualJudgeAssignment, ManualJudgeAssignmentAdmin)
admin.site.register(models.AuditEvent, AuditEventAdmin)
