from django.http import HttpResponse
from django.shortcuts import render

from mittab.apps.tab.forms import JudgeForm, ScratchForm
from mittab.apps.tab.helpers import redirect_and_flash_error, redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs.errors import *
from mittab.libs.tab_logic import TabFlags
from mittab.apps.tab.spreadsheet_utils import spreadsheet_view


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
    current_round = TabSettings.get("cur_round", 1) - 1
    checkins = CheckIn.objects.filter(round_number=current_round)
    checkins_next = CheckIn.objects.filter(round_number=current_round + 1)
    checked_in_judges = {c.judge_id for c in checkins}
    checked_in_judges_next = {c.judge_id for c in checkins_next}

    def with_checkin_flags(judge):
        return {
            "current": judge.id in checked_in_judges,
            "next": judge.id in checked_in_judges_next,
        }

    def checkin_label(flags, round_label):
        return "Yes" if flags.get(round_label) else "No"

    judge_config = {
        "title": "Manage Judges",
        "model": Judge,
        "queryset": lambda: Judge.objects.select_related("school").prefetch_related("schools").order_by("name"),
        "columns": [
            {
                "name": "id",
                "title": "ID",
                "type": "text",
                "width": 80,
                "read_only": True,
            },
            {
                "name": "name",
                "title": "Name",
                "type": "text",
                "required": True,
            },
            {
                "name": "rank",
                "title": "Rank",
                "type": "numeric",
                "mask": "0",
                "python_type": "int",
                "required": True,
                "min_value": 1,
                "max_value": 99,
            },
            {
                "name": "ballot_code",
                "title": "Ballot Code",
                "type": "text",
                "required": False,
            },
            {
                "name": "wing_only",
                "title": "Wing Only",
                "type": "checkbox",
                "python_type": "bool",
            },
            {
                "name": "is_dino",
                "title": "Dino",
                "type": "checkbox",
                "python_type": "bool",
            },
            {
                "name": "schools",
                "title": "Schools",
                "type": "text",
                "read_only": True,
                "skip_model_field": True,
                "value_getter": lambda judge: ", ".join(sorted(school.name for school in judge.schools.all())) or None,
            },
            {
                "name": "checked_in_current",
                "title": "Checked In (Current)",
                "type": "text",
                "read_only": True,
                "skip_model_field": True,
                "value_getter": lambda judge: checkin_label(with_checkin_flags(judge), "current"),
            },
            {
                "name": "checked_in_next",
                "title": "Checked In (Next)",
                "type": "text",
                "read_only": True,
                "skip_model_field": True,
                "value_getter": lambda judge: checkin_label(with_checkin_flags(judge), "next"),
            },
        ],
        "allow_create": True,
    }

    return spreadsheet_view(request, judge_config)


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
            return redirect_and_flash_success(
                request, "Judge {} updated successfully".format(
                    form.cleaned_data["name"]))
    else:
        form = JudgeForm(instance=judge)
        judging_rounds = list(Round.objects.filter(judges=judge).select_related(
            "gov_team", "opp_team", "room"))
    base_url = f"/judge/{judge_id}/"
    scratch_url = f"{base_url}scratches/view/"
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
            return redirect_and_flash_success(
                request,
                "Judge {} created successfully".format(
                    form.cleaned_data["name"]),
                path="/")
    else:
        form = JudgeForm(first_entry=True)
    return render(request, "common/data_entry.html", {
        "form": form,
        "title": "Create Judge"
    })


def add_scratches(request, judge_id, number_scratches):
    try:
        judge_id, number_scratches = int(judge_id), int(number_scratches)
    except ValueError:
        return redirect_and_flash_error(request, "Got invalid data")
    try:
        judge = Judge.objects.get(pk=judge_id)
    except Judge.DoesNotExist:
        return redirect_and_flash_error(request, "No such judge")

    if request.method == "POST":
        forms = [
            ScratchForm(request.POST, prefix=str(i))
            for i in range(1, number_scratches + 1)
        ]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            for form in forms:
                form.save()
            return redirect_and_flash_success(
                request, "Scratches created successfully")
    else:
        forms = [
            ScratchForm(
                prefix=str(i),
                initial={
                    "judge": judge_id,
                    "scratch_type": 0
                }
            )
            for i in range(1, number_scratches + 1)
        ]
    return render(
        request, "common/data_entry_multiple.html", {
            "forms": list(zip(forms, [None] * len(forms))),
            "data_type": "Scratch",
            "title": "Adding Scratch(es) for %s" % (judge.name)
        })


def view_scratches(request, judge_id):
    try:
        judge_id = int(judge_id)
    except ValueError:
        return redirect_and_flash_error(request, "Received invalid data")

    judge = Judge.objects.prefetch_related(
        "scratches", "scratches__judge", "scratches__team"
    ).get(pk=judge_id)
    scratches = judge.scratches.all()

    all_teams = Team.objects.all()
    all_judges = Judge.objects.all()

    if request.method == "POST":
        forms = [
            ScratchForm(
                request.POST,
                prefix=str(i + 1),
                instance=scratches[i],
                team_queryset=all_teams,
                judge_queryset=all_judges
            )
            for i in range(len(scratches))
        ]
        all_good = True
        for form in forms:
            all_good = all_good and form.is_valid()
        if all_good:
            for form in forms:
                form.save()
            return redirect_and_flash_success(
                request, "Scratches created successfully")
    else:
        forms = [
            ScratchForm(
                prefix=str(i + 1),
                instance=scratches[i],
                team_queryset=all_teams,
                judge_queryset=all_judges
            )
            for i in range(len(scratches))
        ]
    delete_links = [
        "/judge/" + str(judge_id) + "/scratches/delete/" + str(scratches[i].id)
        for i in range(len(scratches))
    ]
    links = [("/judge/" + str(judge_id) + "/scratches/add/1/", "Add Scratch")]

    return render(
        request, "common/data_entry_multiple.html", {
            "forms": list(zip(forms, delete_links)),
            "data_type": "Scratch",
            "links": links,
            "title": "Viewing Scratch Information for %s" % (judge.name)
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
