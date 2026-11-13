"""Tournament-admin editing of public history, separate from immutable audits."""

from datetime import timedelta, timezone as datetime_timezone

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from mittab.apps.tab.models import PublicRosterChange
from mittab.apps.tab.public_roster_log import log_state, logging_enabled


EST = datetime_timezone(timedelta(hours=-5))


class ESTDateTimeField(forms.DateTimeField):
    def to_python(self, value):
        with timezone.override(EST):
            return super().to_python(value)


class PublicRosterChangeForm(forms.ModelForm):
    occurred_at = ESTDateTimeField(
        label="Date and time (EST)",
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        help_text="Fixed EST (UTC−5), including during daylight saving time.",
    )

    class Meta:
        model = PublicRosterChange
        fields = ("list_kind", "message", "occurred_at", "involves_debaters")
        labels = {
            "list_kind": "Public list",
            "involves_debaters": "Contains debater names",
        }
        help_texts = {
            "involves_debaters": "Hide this entry when debater names are private.",
        }
        widgets = {"message": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["occurred_at"] = self.instance.created_at.astimezone(EST).replace(
            tzinfo=None
        )

    def save(self, commit=True):
        entry = super().save(commit=False)
        entry.created_at = self.cleaned_data["occurred_at"]
        if commit:
            entry.save()
        return entry


@permission_required("tab.change_publicrosterchange", login_url="/403/")
@require_safe
def manage_roster_log(request):
    entries = PublicRosterChange.objects.all()
    kind = request.GET.get("kind", "")
    if kind in {"teams", "judges"}:
        entries = entries.filter(list_kind=kind)
    else:
        kind = ""
    response = render(
        request,
        "tab/roster_log.html",
        {
            "page": Paginator(entries, 30).get_page(request.GET.get("page")),
            "kind": kind,
            "state": log_state(),
            "logging_enabled": logging_enabled(),
        },
    )
    response["Cache-Control"] = "private, no-store"
    return response


@permission_required("tab.change_publicrosterchange", login_url="/403/")
@require_http_methods(["GET", "HEAD", "POST"])
def edit_roster_log(request, entry_id):
    entry = get_object_or_404(PublicRosterChange, pk=entry_id)
    if request.method == "POST" and request.POST.get("action") == "delete":
        if not request.user.has_perm("tab.delete_publicrosterchange"):
            return HttpResponseForbidden("You cannot delete public change log entries.")
        entry.delete()
        messages.success(request, "Public change log entry deleted.")
        return redirect("manage_roster_log")
    form = PublicRosterChangeForm(request.POST or None, instance=entry)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Public change log entry updated.")
        return redirect("manage_roster_log")
    response = render(
        request, "tab/edit_roster_log.html", {"form": form, "entry": entry}
    )
    response["Cache-Control"] = "private, no-store"
    return response
