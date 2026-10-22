from django.contrib import admin

from django import forms

from mittab.apps.tab import models
from mittab.libs.outround_tab_logic.pairing import prepare_challenge_edits
from mittab.apps.tab.board_audit import (
    pairing_snapshot, record_pairing_change, record_pairing_removal,
)


class PairingAuditAdminMixin:
    def save_model(self, request, obj, form, change):
        previous = type(obj).objects.filter(pk=obj.pk).first() if change else None
        form.pairing_before = pairing_snapshot(previous) if previous else None
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        record_pairing_change(form.instance, form.pairing_before, request.user, "admin correction")

    def delete_model(self, request, obj):
        model, object_id, before = type(obj), obj.pk, pairing_snapshot(obj)
        super().delete_model(request, obj)
        record_pairing_removal(model, object_id, before, request.user)

    def delete_queryset(self, request, queryset):
        model = queryset.model
        before = {pairing.pk: pairing_snapshot(pairing) for pairing in queryset}
        super().delete_queryset(request, queryset)
        for object_id, snapshot in before.items():
            record_pairing_removal(model, object_id, snapshot, request.user)


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


class RoundAdmin(PairingAuditAdminMixin, admin.ModelAdmin):
    form = RoundAdminForm
    filter_horizontal = ("judges", )


class OutroundAdmin(PairingAuditAdminMixin, admin.ModelAdmin):
    form = OutroundAdminForm
    filter_horizontal = ("judges", )


class RoundStatsAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        round_ids = {obj.round_id}
        if change:
            old_id = models.RoundStats.objects.filter(pk=obj.pk).values_list(
                "round_id", flat=True).first()
            round_ids.add(old_id)
        rounds = list(models.Round.objects.filter(pk__in=round_ids))
        before = {pairing.pk: pairing_snapshot(pairing) for pairing in rounds}
        super().save_model(request, obj, form, change)
        for pairing in rounds:
            record_pairing_change(pairing, before[pairing.pk], request.user, "admin scores")

    def delete_model(self, request, obj):
        pairing = obj.round
        before = pairing_snapshot(pairing)
        super().delete_model(request, obj)
        record_pairing_change(pairing, before, request.user, "admin scores")

    def delete_queryset(self, request, queryset):
        rounds = list(models.Round.objects.filter(pk__in=queryset.values("round_id")))
        before = {pairing.pk: pairing_snapshot(pairing) for pairing in rounds}
        super().delete_queryset(request, queryset)
        for pairing in rounds:
            record_pairing_change(pairing, before[pairing.pk], request.user, "admin scores")


class AttributionAdminMixin:
    readonly_fields = ("created_by", "created_at")

    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, "created_by") and not obj.created_by_id:
            obj.created_by = request.user
        before_dino = (models.Judge.objects.filter(pk=obj.pk).values("is_dino").first()
                       if isinstance(obj, models.Judge) and change else None)
        super(AttributionAdminMixin, self).save_model(request, obj, form, change)
        event_type = models.AuditEvent.EDIT if change else models.AuditEvent.CREATE
        changes = {"fields": list(form.changed_data)} if change else {}
        if before_dino and before_dino["is_dino"] != obj.is_dino:
            changes["is_dino"] = {"before": before_dino["is_dino"], "after": obj.is_dino}
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
admin.site.register(models.RoundStats, RoundStatsAdmin)
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
