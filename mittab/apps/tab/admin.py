from django import forms
from django.contrib import admin

from mittab.apps.tab.models import *


class RoundAdminForm(forms.ModelForm):
    chair = forms.ModelChoiceField(queryset=Judge.objects.order_by('name'))

    class Meta:
        model = Round

class RoundAdmin(admin.ModelAdmin):
    form = RoundAdminForm
    filter_horizontal = ('judges',)

class TeamAdmin(admin.ModelAdmin):
    filter_horizontal = ('debaters',)

admin.site.register(Debater)
admin.site.register(Team, TeamAdmin)
admin.site.register(School)
admin.site.register(Judge)
admin.site.register(Scratch)
admin.site.register(Round, RoundAdmin)
admin.site.register(RoundStats)
admin.site.register(CheckIn)
admin.site.register(TabSettings)
admin.site.register(Room)
admin.site.register(Bye)
admin.site.register(NoShow)
