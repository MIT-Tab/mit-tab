from django.http import HttpResponse
from django.shortcuts import render

from mittab.apps.tab.forms import JudgeForm
from mittab.apps.tab.helpers import redirect_and_flash_error, redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs.errors import *
from mittab.libs.tab_logic import TabFlags


def public_view_judges(request):
    display_judges = TabSettings.get("judges_public", 0)

    if not request.user.is_authenticated and not display_judges:
        return redirect_and_flash_error(request, "This view is not public", path="/")

    num_rounds = TabSettings.get("tot_rounds", 5)
    rounds = [num for num in range(1, num_rounds + 1)]

    return render(
        request, "public/judges.html", {
            "judges": Judge.objects.order_by("name").all(),
            "rounds": rounds
        })


def view_judges(request):
    # Get a list of (id,school_name) tuples
    current_round = TabSettings.objects.get(key="cur_round").value - 1
    checkins = CheckIn.objects.filter(round_number=current_round)
    checkins_next = CheckIn.objects.filter(round_number=current_round + 1)
    checked_in_judges = set([c.judge for c in checkins])
    checked_in_judges_next = set([c.judge for c in checkins_next])

    def flags(judge):
        result = 0
        if judge in checked_in_judges:
            result |= TabFlags.JUDGE_CHECKED_IN_CUR
        else:
            result |= TabFlags.JUDGE_NOT_CHECKED_IN_CUR
        if judge in checked_in_judges_next:
            result |= TabFlags.JUDGE_CHECKED_IN_NEXT
        else:
            result |= TabFlags.JUDGE_NOT_CHECKED_IN_NEXT

        if judge.rank < 3.0:
            result |= TabFlags.LOW_RANKED_JUDGE
        if judge.rank >= 3.0 and judge.rank < 5.0:
            result |= TabFlags.MID_RANKED_JUDGE
        if judge.rank >= 5.0:
            result |= TabFlags.HIGH_RANKED_JUDGE
        return result

    judges = sorted(Judge.objects.all(), key=lambda j: (-j.rank, j.name))

    c_judge = [
        (judge.pk, judge.name, flags(judge), f"({judge.ballot_code})", judge.rank)
        for judge in judges
    ]

    all_flags = [
        [
            TabFlags.JUDGE_CHECKED_IN_CUR,
            TabFlags.JUDGE_NOT_CHECKED_IN_CUR,
            TabFlags.JUDGE_CHECKED_IN_NEXT,
            TabFlags.JUDGE_NOT_CHECKED_IN_NEXT,
        ],
        [
            TabFlags.LOW_RANKED_JUDGE,
            TabFlags.MID_RANKED_JUDGE,
            TabFlags.HIGH_RANKED_JUDGE,
        ]
    ]
    filters, _symbol_text = TabFlags.get_filters_and_symbols(all_flags)
    return render(
        request, "common/list_data.html", {
            "item_type": "judge",
            "title": "Viewing All Judges",
            "item_list": c_judge,
            "filters": filters,
        })


def view_judge(request, judge_id):
    judge_id = int(judge_id)
    judging_rounds = []
    try:
        judge = Judge.objects.get(pk=judge_id)
    except Judge.DoesNotExist:
        return redirect_and_flash_error(request, "Judge not found")
    if request.method == "POST":
        form = JudgeForm(request.POST, instance=judge)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(
                    request, "Judge information cannot be validated")
            updated_name = form.cleaned_data["name"]
            return redirect_and_flash_success(
                request, f"Judge {updated_name} updated successfully")
    else:
        form = JudgeForm(instance=judge)
        judging_rounds = list(Round.objects.filter(judges=judge).select_related(
            "gov_team", "opp_team", "room"))
    scratch_url = f"/scratches/judge/{judge_id}/"
    links = [(scratch_url, f"Scratches for {judge.name}")]
    return render(
        request, "tab/judge_detail.html", {
            "form": form,
            "links": links,
            "judge_rounds": judging_rounds,
            "title": f"Viewing Judge: {judge.name}"
        })


def enter_judge(request):
    if request.method == "POST":
        form = JudgeForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ValueError:
                return redirect_and_flash_error(request,
                                                "Judge cannot be validated")
            created_name = form.cleaned_data["name"]
            return redirect_and_flash_success(
                request,
                f"Judge {created_name} created successfully",
                path="/")
    else:
        form = JudgeForm(first_entry=True)
    return render(request, "common/data_entry.html", {
        "form": form,
        "title": "Create Judge"
    })


def download_judge_codes(request):
    codes = [
        f"{getattr(judge, 'name', 'Unknown')}: {getattr(judge, 'ballot_code', 'N/A')}"
        for judge in Judge.objects.all()
    ]
    response_content = "\n".join(codes)
    response = HttpResponse(response_content, content_type="text/plain")
    response["Content-Disposition"] = "attachment; filename=judge_codes.txt"
    return response
