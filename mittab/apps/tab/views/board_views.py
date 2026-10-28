"""Dedicated board views with explicit safe fields and no judge candidate APIs."""
from functools import wraps

from django import forms
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import (
    require_safe, require_POST, require_http_methods,
)

from mittab.apps.tab.auth_roles import is_apda_board_access_open, is_apda_board_user
from mittab.apps.tab.board_audit import (
    deleted_pairings, manual_inround_numbers, manual_team_positions, pairing_history,
    pairing_revision, pairing_snapshot, with_pairing_snapshot_relations,
)
from mittab.apps.tab.board_operations import (
    bracket_problems, edit_board_outround, lock_board_tournament, pair_board_outround,
)
from mittab.apps.tab.helpers import redirect_and_flash_error, redirect_and_flash_success
from mittab.apps.tab.judge_history import current_manual_judge_events
from mittab.apps.tab.models import (
    AuditEvent, BreakingTeam, Bye, Judge, NoShow, Outround, Room, Round, Team,
)
from mittab.libs.tab_logic.stats import (
    tot_ranks, tot_ranks_deb, tot_speaks, tot_speaks_deb,
)


def board_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_apda_board_user(request.user) or not is_apda_board_access_open():
            return HttpResponseForbidden("Board access is unavailable.")
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response
    return wrapper


def pairing_rows(queryset):
    pairings = list(with_pairing_snapshot_relations(queryset))
    events_by_pairing = {}
    judge_names_by_pairing = {}
    if pairings:
        pairing_ids = [pairing.pk for pairing in pairings]
        for event in AuditEvent.objects.filter(
                content_type=ContentType.objects.get_for_model(queryset.model),
                object_id__in=pairing_ids,
        ).select_related("user").order_by("created_at", "id"):
            events_by_pairing.setdefault(event.object_id, []).append(event)
        for event in current_manual_judge_events(queryset.model, pairing_ids):
            if isinstance(event.changes, dict):
                judge_names_by_pairing.setdefault(event.object_id, []).extend(
                    event.changes[field] for field in ("assigned_judge", "chair", "new_chair")
                    if isinstance(event.changes.get(field), str))
    rows = []
    for pairing in pairings:
        snapshot = pairing_snapshot(pairing)
        history = pairing_history(type(pairing), pairing.pk,
                                  events_by_pairing.get(pairing.pk, []))
        rows.append(dict(snapshot, id=pairing.pk, revision=pairing_revision(snapshot),
                         outcome=pairing.get_victor_display(), history=history,
                         manual_team_positions=manual_team_positions(history),
                         manual_judge_names=judge_names_by_pairing.get(pairing.pk, [])))
    return rows


@board_required
@require_safe
def inrounds(request, round_number=None):
    numbers = list(Round.objects.order_by("round_number").values_list(
        "round_number", flat=True).distinct())
    manual_only = request.GET.get("manual") == "1"
    if manual_only:
        numbers = manual_inround_numbers()
    if round_number is None or (manual_only and round_number not in numbers):
        round_number = numbers[-1] if numbers else None
    rows = pairing_rows(Round.objects.filter(round_number=round_number).order_by("id"))
    if manual_only:
        rows = [row for row in rows if row["history"]]
    return render(request, "apda_board/inrounds.html", {
        "pairings": rows, "round_number": round_number, "round_numbers": numbers,
        "manual_only": manual_only,
        "deleted": deleted_pairings(Round, "round_number", round_number),
    })


@board_required
@require_safe
def outrounds(request):
    divisions = []
    for division, label in Outround.TYPE_OF_ROUND_CHOICES:
        stages = []
        queryset = Outround.objects.filter(type_of_round=division)
        for size in queryset.order_by(
                "-num_teams").values_list("num_teams", flat=True).distinct():
            stages.append({"size": size, "pairings": pairing_rows(
                queryset.filter(num_teams=size).order_by("id"))})
        teams = list(BreakingTeam.objects.filter(type_of_team=division).order_by("seed")
                     .values("team_id", "team__name", "seed", "effective_seed"))
        divisions.append({"id": division, "label": label, "stages": stages,
                          "teams": teams, "problems": bracket_problems(division),
                          "deleted": deleted_pairings(Outround, "division", division)})
    return render(request, "apda_board/outrounds.html", {
        "divisions": divisions,
        "rooms": list(Room.objects.order_by("name", "id").values("id", "name")),
        "outcomes": Outround.VICTOR_CHOICES,
    })


class CorrectionForm(forms.Form):
    revision = forms.CharField(max_length=64)
    action = forms.ChoiceField(choices=[(name, name) for name in
                                        ("result", "sides", "team", "room", "choice")])
    victor = forms.TypedChoiceField(
        choices=Outround.VICTOR_CHOICES, coerce=int, required=False)
    position = forms.ChoiceField(
        choices=(("gov", "Gov"), ("opp", "Opp")), required=False)
    team = forms.IntegerField(min_value=1, required=False)
    room = forms.IntegerField(min_value=1, required=False)
    choice = forms.TypedChoiceField(
        choices=Outround.CHOICES, coerce=int, required=False)

    def clean(self):
        data = super().clean()
        required = {"result": ("victor",), "team": ("position", "team"),
                    "choice": ("choice",)}.get(data.get("action"), ())
        for field in required:
            if data.get(field) in (None, ""):
                self.add_error(field, "This field is required.")
        return data


@board_required
@require_POST
def edit_outround(request, round_id):
    get_object_or_404(Outround, pk=round_id)
    form = CorrectionForm(request.POST)
    if not form.is_valid():
        return redirect_and_flash_error(
            request,
            "Invalid correction.",
            path="/apda-board/outrounds/")
    try:
        edit_board_outround(
            round_id, form.cleaned_data["action"], form.cleaned_data, request.user)
    except ValidationError as error:
        return redirect_and_flash_error(request, " ".join(
            error.messages), path="/apda-board/outrounds/")
    return redirect_and_flash_success(
        request,
        "Correction saved. Review later rounds after changing teams or outcomes.",
        path="/apda-board/outrounds/",
    )


@board_required
@require_POST
def pair_outround(request):
    try:
        division = int(request.POST.get("division", ""))
        pair_board_outround(division, request.user)
    except (ValueError, ValidationError) as error:
        message = " ".join(error.messages) if isinstance(
            error, ValidationError) else "Choose a division."
        return redirect_and_flash_error(request, message, path="/apda-board/outrounds/")
    return redirect_and_flash_success(
        request,
        "Teams and rooms paired.",
        path="/apda-board/outrounds/")


@board_required
@require_http_methods(["GET", "HEAD", "POST"])
def judges(request):
    if request.method == "POST":
        try:
            judge_id = int(request.POST.get("judge", ""))
            is_dino = request.POST.get("is_dino") == "on"
            with transaction.atomic():
                lock_board_tournament(request.user)
                judge = get_object_or_404(
                    Judge.objects.select_for_update().only(
                        "id", "name", "is_dino"), pk=judge_id)
                before = judge.is_dino
                # QuerySet update deliberately avoids Judge.save()/ballot-code
                # generation.
                Judge.objects.filter(pk=judge.pk).update(is_dino=is_dino)
                if before != is_dino:
                    AuditEvent.record(judge, AuditEvent.EDIT, request.user, changes={
                        "is_dino": {"before": before, "after": is_dino},
                    })
        except (ValueError, ValidationError):
            return redirect_and_flash_error(
                request, "Invalid judge update.", path=request.path)
        return redirect("board_judges")
    roster = list(Judge.objects.order_by("name", "id").values("id", "name", "is_dino"))
    histories = {}
    events = AuditEvent.objects.filter(
        content_type=ContentType.objects.get_for_model(Judge)).select_related("user")
    for event in events:
        change = (event.changes.get("is_dino")
                  if isinstance(event.changes, dict) else None)
        if (isinstance(change, dict) and isinstance(change.get("before"), bool)
                and isinstance(change.get("after"), bool)):
            histories.setdefault(event.object_id, []).append({
                "actor": event.user_display, "time": event.created_at,
                "before": change["before"], "after": change["after"],
            })
    for judge in roster:
        judge["history"] = histories.get(judge["id"], [])
    return render(request, "apda_board/judges.html", {"judges": roster})


@board_required
@require_safe
def tab_cards(request, team_id=None):
    teams = list(Team.objects.order_by("name", "id").values("id", "name"))
    team = get_object_or_404(Team.objects.only("id", "name"),
                             pk=team_id) if team_id else None
    pairings = []
    byes, no_shows, debaters = [], [], []
    totals = {}
    if team:
        condition = Q(gov_team_id=team.pk) | Q(opp_team_id=team.pk)
        pairings = pairing_rows(Round.objects.filter(
            condition).order_by("round_number", "id"))
        debaters = [{"id": debater.pk, "name": debater.name,
                     "novice_status": debater.novice_status,
                     "speaks": tot_speaks_deb(debater), "ranks": tot_ranks_deb(debater)}
                    for debater in team.debaters.only("id", "name", "novice_status")]
        totals = {"speaks": tot_speaks(team), "ranks": tot_ranks(team)}
        speaker_ids = {debater["id"] for debater in debaters}
        for pairing in pairings:
            pairing["scores"] = [score for score in pairing["scores"]
                                 if score["debater"] in speaker_ids]
        byes = list(Bye.objects.filter(bye_team_id=team.pk).order_by("round_number")
                    .values_list("round_number", flat=True))
        no_shows = list(
            NoShow.objects.filter(
                no_show_team_id=team.pk).order_by("round_number") .values_list(
                "round_number", flat=True))
    return render(request, "apda_board/tab_cards.html", {
        "teams": teams, "team": {"id": team.pk, "name": team.name} if team else None,
        "pairings": pairings,
        "debaters": debaters, "byes": byes, "no_shows": no_shows,
        "totals": totals,
    })
