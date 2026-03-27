from django.contrib.auth.decorators import permission_required
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

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

SCRATCH_TAB_TYPES = {"judge_team", "judge_judge", "team_team"}


def _get_form_kwargs(scratch_type):
    if scratch_type == "judge-team":
        return {
            "judge_queryset": Judge.objects.order_by("name"),
            "team_queryset": Team.objects.order_by("name"),
        }
    if scratch_type == "judge-judge":
        return {"judge_queryset": Judge.objects.order_by("name")}
    if scratch_type == "team-team":
        return {"team_queryset": Team.objects.order_by("name")}
    return {}


def _build_form_for_instance(scratch_type, instance, data=None, prefix=None):
    form_class = SCRATCH_FORMS[scratch_type]
    return form_class(
        data,
        instance=instance,
        prefix=prefix,
        **_get_form_kwargs(scratch_type),
    )


def _serialize_entry(scratch_type, instance):
    return {
        "scratch_type": scratch_type,
        "instance": instance,
    }


def _get_scratch_entries_for_judge(judge_id):
    entries = [
        _serialize_entry("judge-team", scratch)
        for scratch in Scratch.objects.filter(judge_id=judge_id).select_related(
            "judge", "team"
        ).order_by("team__name", "judge__name")
    ]
    entries.extend(
        _serialize_entry("judge-judge", scratch)
        for scratch in JudgeJudgeScratch.objects.filter(
            Q(judge_one_id=judge_id) | Q(judge_two_id=judge_id)
        ).select_related("judge_one", "judge_two").order_by(
            "judge_one__name", "judge_two__name"
        )
    )
    return entries


def _get_scratch_entries_for_team(team_id):
    entries = [
        _serialize_entry("judge-team", scratch)
        for scratch in Scratch.objects.filter(team_id=team_id).select_related(
            "judge", "team"
        ).order_by("team__name", "judge__name")
    ]
    entries.extend(
        _serialize_entry("team-team", scratch)
        for scratch in TeamTeamScratch.objects.filter(
            Q(team_one_id=team_id) | Q(team_two_id=team_id)
        ).select_related("team_one", "team_two").order_by(
            "team_one__name", "team_two__name"
        )
    )
    return entries


def _build_object_forms(entries, data=None):
    forms = []
    for index, entry in enumerate(entries, start=1):
        form = _build_form_for_instance(
            entry["scratch_type"],
            entry["instance"],
            data=data,
            prefix=str(index),
        )
        forms.append(
            (
                form,
                reverse(
                    "scratch_delete",
                    args=(entry["scratch_type"], entry["instance"].id),
                ),
            )
        )
    return forms


def _parse_scratch_count(raw_count):
    try:
        return max(1, int(raw_count))
    except (TypeError, ValueError):
        return 1


def add_scratch(request):
    judges = Judge.objects.order_by("name")
    teams = Team.objects.order_by("name")

    judge_id, team_id = request.GET.get("judge_id"), request.GET.get("team_id")
    active_tab = request.POST.get("form_type") or request.GET.get("tab") or "judge_team"
    if active_tab not in SCRATCH_TAB_TYPES:
        active_tab = "judge_team"
    form_count = _parse_scratch_count(
        request.POST.get("count", request.GET.get("count"))
    )

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
            request.POST if (is_post and active_tab == prefix) else None
        )
        return [
            form_cls(
                data,
                prefix=f"{prefix}_{index}",
                **queryset_args,
                initial=None if data else initial,
            )
            for index in range(form_count)
        ]

    forms_by_type = {
        "judge_team": make_form(
            ScratchForm,
            "judge_team",
            {"judge_queryset": judges, "team_queryset": teams},
            scratch_initial,
        ),
        "judge_judge": make_form(
            JudgeJudgeScratchForm,
            "judge_judge",
            {"judge_queryset": judges},
            judge_pair_initial,
        ),
        "team_team": make_form(
            TeamTeamScratchForm,
            "team_team",
            {"team_queryset": teams},
            team_pair_initial,
        ),
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
                {"key": k, "label": v, "forms": forms_by_type[k], "count": form_count}
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


def view_scratches_for_object(request, object_id, object_type):
    try:
        object_id = int(object_id)
    except ValueError:
        return redirect_and_flash_error(request, "Received invalid data")

    if object_type == "judge":
        obj = get_object_or_404(Judge, pk=object_id)
        entries = _get_scratch_entries_for_judge(object_id)
        add_query = f"?judge_id={object_id}&tab=judge_team"
    elif object_type == "team":
        obj = get_object_or_404(Team, pk=object_id)
        entries = _get_scratch_entries_for_team(object_id)
        add_query = f"?team_id={object_id}&tab=judge_team"
    else:
        return redirect_and_flash_error(request, "Unknown object type")

    forms = _build_object_forms(
        entries,
        data=request.POST if request.method == "POST" else None,
    )
    if request.method == "POST" and all(form.is_valid() for form, _ in forms):
        try:
            with transaction.atomic():
                for form, _ in forms:
                    form.save()
        except IntegrityError:
            for form, _ in forms:
                form.add_error(None, "This scratch already exists.")
        else:
            return redirect_and_flash_success(
                request,
                "Scratches successfully modified",
                path=request.get_full_path(),
            )

    return render(
        request,
        "common/data_entry_multiple.html",
        {
            "title": f"Viewing Scratch Information for {obj}",
            "data_type": "Scratch",
            "forms": forms,
            "links": [(reverse("add_scratch") + add_query, "Add Scratch")],
        },
    )
def scratch_detail(request, scratch_type, scratch_id):
    model = SCRATCH_OBJECTS.get(scratch_type)
    if not model:
        return redirect_and_flash_error(
            request, "Unknown scratch type", path=reverse("view_scratches")
        )
    scratch_obj = get_object_or_404(model, pk=scratch_id)
    form_obj = _build_form_for_instance(
        scratch_type,
        scratch_obj,
        data=request.POST if request.method == "POST" else None,
    )
    if request.method == "POST" and form_obj.is_valid():
        try:
            form_obj.save()
        except IntegrityError:
            form_obj.add_error(None, "This scratch already exists.")
        else:
            return redirect_and_flash_success(
                request,
                "Scratch updated successfully",
                path=request.get_full_path(),
            )
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
