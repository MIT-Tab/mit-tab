from django.contrib.auth.decorators import permission_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, render, reverse

from mittab.apps.tab.forms import (
    ScratchForm,
    JudgeJudgeScratchForm,
    TeamTeamScratchForm,
)
from mittab.apps.tab.helpers import (
    redirect_and_flash_error,
    redirect_and_flash_success,
)
from mittab.apps.tab.models import (
    Judge,
    Team,
    Scratch,
    JudgeJudgeScratch,
    TeamTeamScratch,
)

SCRATCH_OBJECTS = {
    "judge-team": Scratch,
    "judge-judge": JudgeJudgeScratch,
    "team-team": TeamTeamScratch,
}

SCRATCH_FORMS = {
    "judge-team": ScratchForm,
    "judge-judge": JudgeJudgeScratchForm,
    "team-team": TeamTeamScratchForm,
}


def add_scratch(request):
    judges = Judge.objects.order_by("name")
    teams = Team.objects.order_by("name")

    judge_id, team_id = request.GET.get("judge_id"), request.GET.get("team_id")
    active_tab = request.POST.get("form_type") or request.GET.get("tab") or "judge_team"
    if active_tab not in {"judge_team", "judge_judge", "team_team"}:
        active_tab = "judge_team"

    is_post = request.method == "POST"

    # shared initial data
    scratch_initial = {
        "scratch_type": Scratch.TEAM_SCRATCH,
        "judge": judge_id,
        "team": team_id,
    }
    judge_pair_initial = {"judge_one": judge_id} if judge_id else {}
    team_pair_initial = {"team_one": team_id} if team_id else {}

    def make_form(form_cls, prefix, queryset_args, initial):
        data = (
            request.POST if (is_post and active_tab == prefix.split("_")[0]) else None
        )
        return form_cls(
            data,
            prefix=f"{prefix}_0",
            **queryset_args,
            initial=None if data else initial,
        )

    forms_by_type = {
        "judge_team": [
            make_form(
                ScratchForm,
                "judge_team",
                {"judge_queryset": judges, "team_queryset": teams},
                scratch_initial,
            )
        ],
        "judge_judge": [
            make_form(
                JudgeJudgeScratchForm,
                "judge_judge",
                {"judge_queryset": judges},
                judge_pair_initial,
            )
        ],
        "team_team": [
            make_form(
                TeamTeamScratchForm,
                "team_team",
                {"team_queryset": teams},
                team_pair_initial,
            )
        ],
    }

    if is_post:
        forms = forms_by_type[active_tab]
        if all(f.is_valid() for f in forms):
            try:
                with transaction.atomic():
                    for f in forms:
                        f.save()
            except IntegrityError:
                for f in forms:
                    f.add_error(None, "This scratch already exists.")
            else:
                return redirect_and_flash_success(
                    request,
                    "Scratches created successfully",
                    path=request.get_full_path(),
                )

    tab_labels = {
        "judge_team": "Judge ↔ Team",
        "judge_judge": "Judge ↔ Judge",
        "team_team": "Team ↔ Team",
    }

    return render(
        request,
        "scratches/add_scratches.html",
        {
            "forms_by_type": forms_by_type,
            "tabs": list(tab_labels.items()),
            "forms_context": [
                {"key": k, "label": v, "forms": forms_by_type[k]}
                for k, v in tab_labels.items()
            ],
            "active_tab": active_tab,
        },
    )


SCRATCH_FILTER_DEFS = {
    "judge-team": {"bit": 1, "label": "Judge ↔ Team"},
    "judge-judge": {"bit": 2, "label": "Judge ↔ Judge"},
    "team-team": {"bit": 4, "label": "Team ↔ Team"},
}


def view_scratches(request):
    def build_items(qs, type_key, labels):
        """Build (id, name, bitmask, symbols) tuples for each scratch type."""
        bit = SCRATCH_FILTER_DEFS[type_key]["bit"]
        items = []
        for s in qs:
            left_obj = getattr(s, labels[0])
            right_obj = getattr(s, labels[1])
            left_name = (
                left_obj.display_backend
                if isinstance(left_obj, Team) or "team" in labels[0]
                else left_obj.name
            )
            right_name = (
                right_obj.display_backend
                if isinstance(right_obj, Team) or "team" in labels[1]
                else right_obj.name
            )
            item_id = f"{type_key}/{s.id}"
            item_label = left_name + " ↔ " + right_name
            items.append((item_id, item_label, bit, ""))
        return items

    configs = [
        (
            "judge-team",
            Scratch.objects.select_related("team", "judge").order_by(
                "team__name", "judge__name"
            ),
            ("team", "judge"),
        ),
        (
            "judge-judge",
            JudgeJudgeScratch.objects.select_related("judge_one", "judge_two").order_by(
                "judge_one__name", "judge_two__name"
            ),
            ("judge_one", "judge_two"),
        ),
        (
            "team-team",
            TeamTeamScratch.objects.select_related("team_one", "team_two").order_by(
                "team_one__name", "team_two__name"
            ),
            ("team_one", "team_two"),
        ),
    ]

    # Flatten all items
    item_list = [
        item for key, qs, labels in configs for item in build_items(qs, key, labels)
    ]

    # Each filter group entry should be (bit, label)
    filters_group = [(v["bit"], v["label"]) for v in SCRATCH_FILTER_DEFS.values()]

    return render(
        request,
        "common/list_data.html",
        {
            "title": "Scratches",
            "item_type": "scratch",
            "item_list": item_list,
            "filters": [filters_group],
        },
    )


def view_scratches_for_object(request, object_type, object_id):
    try:
        object_id = int(object_id)
        obj = get_object_or_404(Judge if object_type == "judge" else Team, pk=object_id)
    except ValueError:
        return redirect_and_flash_error(request, "Received invalid data")
    if object_type == "judge":
        forms = get_scratch_forms_for_judge(object_id)
    elif object_type == "team":
        forms = get_scratch_forms_for_team(object_id)
    else:
        return redirect_and_flash_error(request, "Unknown object type")

    return render(
        request,
        "common/data_entry_multiple.html",
        {
            "title": f"Viewing Scratch Information for {obj}",
            "data_type": "Scratch",
            "forms": [(form, None) for form in forms],
        },
    )


def get_scratch_forms_for_judge(judge_id):
    forms = []
    for scratch in Scratch.objects.filter(judge_id=judge_id).select_related("team"):
        form = ScratchForm(
            instance=scratch,
            prefix=str(len(forms) + 1),
            team_queryset=Team.objects.order_by("name"),
            judge_queryset=Judge.objects.order_by("name"),
        )
        forms.append(form)
    for scratch in JudgeJudgeScratch.objects.filter(
        judge_one_id=judge_id
    ).select_related("judge_two") | JudgeJudgeScratch.objects.filter(
        judge_two_id=judge_id
    ).select_related(
        "judge_one"
    ):
        form = JudgeJudgeScratchForm(
            instance=scratch,
            prefix=str(len(forms) + 1),
            judge_queryset=Judge.objects.order_by("name"),
        )
        forms.append(form)
    return forms


def get_scratch_forms_for_team(team_id):
    forms = []
    for scratch in Scratch.objects.filter(team_id=team_id).select_related("judge"):
        form = ScratchForm(
            instance=scratch,
            prefix=str(len(forms) + 1),
            team_queryset=Team.objects.order_by("name"),
            judge_queryset=Judge.objects.order_by("name"),
        )
        forms.append(form)
    for scratch in TeamTeamScratch.objects.filter(team_one_id=team_id).select_related(
        "team_two"
    ) | TeamTeamScratch.objects.filter(team_two_id=team_id).select_related("team_one"):
        form = TeamTeamScratchForm(
            instance=scratch,
            prefix=str(len(forms) + 1),
            team_queryset=Team.objects.order_by("name"),
        )
        forms.append(form)
    return forms

# Backwards-compatible aliases for callers that use the old camelCase names
getScratchFormsForJudge = get_scratch_forms_for_judge
getScratchFormsForTeam = get_scratch_forms_for_team


def scratch_detail(request, scratch_type, scratch_id):
    model = SCRATCH_OBJECTS.get(scratch_type)
    form_class = SCRATCH_FORMS.get(scratch_type)
    scratch_obj = model.objects.get(pk=scratch_id)
    form_obj = form_class(instance=scratch_obj)
    return render(
        request,
        "common/data_entry.html",
        {
            "title": f"Viewing Scratch: {scratch_obj}",
            "data_type": "Scratch",
            "form": form_obj,
            "delete_link": reverse("scratch_delete", args=(scratch_type, scratch_id)),
        },
    )


@permission_required("tab.scratch.can_delete", login_url="/403/")
def scratch_delete(request, scratch_type, scratch_id):
    model = SCRATCH_OBJECTS.get(scratch_type)
    if not model:
        return redirect_and_flash_error(
            request, "Unknown scratch type", path=reverse("view_scratches")
        )
    try:
        scratch = model.objects.get(pk=scratch_id)
    except model.DoesNotExist:
        return redirect_and_flash_error(
            request, "Scratch not found", path=reverse("view_scratches")
        )

    scratch.delete()
    return redirect_and_flash_success(
        request, "Scratch deleted successfully", path=reverse("view_scratches")
    )
