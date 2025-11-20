from django.contrib import admin
from django import forms

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
admin.site.register(models.Room)
admin.site.register(models.RoomTag)
admin.site.register(models.RankingGroup)
admin.site.register(models.Bye)
admin.site.register(models.NoShow)
admin.site.register(models.BreakingTeam)
admin.site.register(models.Outround, OutroundAdmin)
