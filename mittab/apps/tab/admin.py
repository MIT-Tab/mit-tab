from django.contrib import admin
from django import forms
from django.utils.html import format_html

from mittab.apps.tab import models


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


class TeamAdmin(admin.ModelAdmin):
    filter_horizontal = ("debaters", )


class PublicHomeShortcutAdmin(admin.ModelAdmin):
    list_display = ("position", "nav_item", "preview_destination")
    list_editable = ("nav_item",)
    ordering = ("position",)

    def preview_destination(self, obj):
        definition = models.PublicHomeShortcut.nav_definition_map().get(obj.nav_item)
        if definition is None:
            return "-"
        url = definition["url_path"]
        return format_html('<a href="{url}" target="_blank">{url}</a>', url=url)

    preview_destination.short_description = "Destination"


class PublicHomePageAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "url_path", "sort_order", "is_active")
    list_editable = ("url_path", "sort_order", "is_active")
    search_fields = ("title", "slug", "url_path")
    ordering = ("sort_order", "title")


class MotionAdmin(admin.ModelAdmin):
    list_display = ("round_display", "motion_text_truncated", "is_published", "updated_at")
    list_filter = ("is_published", "outround_type")
    search_fields = ("motion_text", "info_slide")
    ordering = ("round_number", "outround_type", "-num_teams")

    def motion_text_truncated(self, obj):
        return obj.motion_text[:100] + "..." if len(obj.motion_text) > 100 else obj.motion_text
    motion_text_truncated.short_description = "Motion Text"


admin.site.register(models.Debater)
admin.site.register(models.Team, TeamAdmin)
admin.site.register(models.School)
admin.site.register(models.Judge)
admin.site.register(models.Scratch)
admin.site.register(models.Round, RoundAdmin)
admin.site.register(models.RoundStats)
admin.site.register(models.CheckIn)
admin.site.register(models.RoomCheckIn)
admin.site.register(models.TabSettings)
admin.site.register(models.PublicHomePage, PublicHomePageAdmin)
admin.site.register(models.PublicHomeShortcut, PublicHomeShortcutAdmin)
admin.site.register(models.Room)
admin.site.register(models.RoomTag)
admin.site.register(models.Bye)
admin.site.register(models.NoShow)
admin.site.register(models.BreakingTeam)
admin.site.register(models.Outround, OutroundAdmin)
admin.site.register(models.Motion, MotionAdmin)
