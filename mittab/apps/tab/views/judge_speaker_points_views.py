"""Staff-only judge speaker points report."""

from django import forms
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from mittab.apps.tab.judge_speaker_points import judge_speaker_metrics, speaker_points_plot

DEFAULT_MINIMUM_ROUNDS = 3


class SpeakerPointsFilter(forms.Form):
    """Read the optional minimum number of scored rounds from the report URL."""

    minimum_rounds = forms.IntegerField(
        min_value=1, initial=DEFAULT_MINIMUM_ROUNDS,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )


@never_cache
@require_safe
def judge_speaker_points(request):
    if not request.user.is_superuser:
        raise PermissionDenied

    filters = SpeakerPointsFilter(
        request.GET if "minimum_rounds" in request.GET else None,
    )
    minimum_rounds = DEFAULT_MINIMUM_ROUNDS
    if filters.is_valid():
        minimum_rounds = filters.cleaned_data["minimum_rounds"]

    metrics = judge_speaker_metrics(minimum_rounds)
    selected = set(request.GET.getlist("judge"))
    for row in metrics:
        row["highlight"] = str(row["id"]) in selected

    midpoint = (len(metrics) + 1) // 2
    return render(request, "tab/judge_speaker_points.html", {
        "filters": filters,
        "minimum_rounds": minimum_rounds,
        "metrics": metrics,
        "judges": sorted(metrics, key=lambda row: row["name"].casefold()),
        "panels": [metrics[:midpoint], metrics[midpoint:]] if len(metrics) > 16 else [metrics],
        "plot": speaker_points_plot(metrics, 760),
        "mobile_plot": speaker_points_plot(metrics, 360),
    })
