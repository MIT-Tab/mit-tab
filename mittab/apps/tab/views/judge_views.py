from collections import Counter
from datetime import timedelta

from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models import Q, Max
from django.utils import timezone

from mittab.apps.tab.forms import JudgeForm, ScratchForm
from mittab.apps.tab.helpers import redirect_and_flash_error, redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs.errors import *
from mittab.libs.tab_logic import TabFlags
from mittab.libs.email_service import EmailRequest, EmailService, EmailServiceError


EMAIL_RATE_LIMIT_WINDOW = timedelta(hours=6)
EMAILS_PER_ADDRESS_PER_WINDOW = 1
EMAILS_PER_ADDRESS_PER_BATCH = 1


def _prepare_judge_code_plan(judges, tournament_name, request):
    eballot_search_url = request.build_absolute_uri(reverse("e_ballot_search"))
    subject = f"{tournament_name} Judge Ballot Code"
    now = timezone.now()
    cutoff = now - EMAIL_RATE_LIMIT_WINDOW

    if not judges:
        return {
            "sendable": [],
            "skipped_invalid": [],
            "skipped_rate_limited": [],
            "skipped_duplicate_email": [],
            "skipped_missing_email": [],
            "recent_judges": set(),
        }

    judge_ids = [judge.id for judge in judges]
    emails_lower = [judge.email.lower() for judge in judges if judge.email]

    recent_logs = JudgeCodeEmailLog.objects.filter(
        sent_at__gte=cutoff,
    )
    if judge_ids or emails_lower:
        recent_logs = recent_logs.filter(
            Q(judge_id__in=judge_ids) | Q(email__in=emails_lower)
        )

    recent_judges = set(recent_logs.values_list("judge_id", flat=True))
    recent_email_counts = Counter(
        email.lower() for email in recent_logs.values_list("email", flat=True)
    )
    batch_email_counts = Counter()

    sendable = []
    skipped_invalid = []
    skipped_rate_limited = []
    skipped_duplicate_email = []
    skipped_missing_email = []

    for judge in judges:
        email = (judge.email or "").strip()
        if not email:
            skipped_missing_email.append((judge, "No email on file"))
            continue
        email_lower = email.lower()

        try:
            judge.is_valid_ballot_code()
        except ValidationError as exc:
            skipped_invalid.append((judge, str(exc)))
            continue

        if judge.id in recent_judges:
            skipped_rate_limited.append((judge, "Judge was emailed recently"))
            continue

        if recent_email_counts[email_lower] >= EMAILS_PER_ADDRESS_PER_WINDOW:
            skipped_rate_limited.append((judge, "Email was contacted recently"))
            continue

        if batch_email_counts[email_lower] >= EMAILS_PER_ADDRESS_PER_BATCH:
            skipped_duplicate_email.append((judge, "Email already queued in this batch"))
            continue

        if not judge.ballot_code:
            judge.set_unique_ballot_code()
            judge.save(update_fields=["ballot_code"])

        if len(judge.ballot_code or "") > BALLOT_CODE_MAX_LENGTH:
            skipped_invalid.append((judge, f"Code exceeds {BALLOT_CODE_MAX_LENGTH} characters"))
            continue

        ballot_url = request.build_absolute_uri(
            reverse("enter_e_ballot", args=[judge.ballot_code])
        )
        text_body = (
            f"Hi {judge.name},\n\n"
            f"Your ballot code for {tournament_name} is {judge.ballot_code}.\n"
            f"Submit e-ballots at {ballot_url} or search at {eballot_search_url}.\n\n"
            "Thank you,\n"
            "Tab Staff"
        )

        email_request = EmailRequest(
            to_address=email,
            subject=subject,
            text_body=text_body,
        )

        log_entry = JudgeCodeEmailLog(
            judge=judge,
            email=email,
            ballot_code=judge.ballot_code,
        )

        sendable.append({
            "judge": judge,
            "email": email,
            "ballot_code": judge.ballot_code,
            "ballot_url": ballot_url,
            "email_request": email_request,
            "log_entry": log_entry,
        })
        recent_email_counts[email_lower] += 1
        batch_email_counts[email_lower] += 1

    return {
        "sendable": sendable,
        "skipped_invalid": skipped_invalid,
        "skipped_rate_limited": skipped_rate_limited,
        "skipped_duplicate_email": skipped_duplicate_email,
        "skipped_missing_email": skipped_missing_email,
        "recent_judges": recent_judges,
    }




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
        (
            judge.pk,
            judge.name,
            flags(judge),
            f"({judge.ballot_code})",
            judge.rank,
            judge.wing_only,
        )
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
            "title": f"Adding Scratch(es) for {judge.name}"
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
        f"/judge/{judge_id}/scratches/delete/{scratches[i].id}"
        for i in range(len(scratches))
    ]
    links = [(f"/judge/{judge_id}/scratches/add/1/", "Add Scratch")]

    return render(
        request, "common/data_entry_multiple.html", {
            "forms": list(zip(forms, delete_links)),
            "data_type": "Scratch",
            "links": links,
            "title": f"Viewing Scratch Information for {judge.name}"
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


def send_judge_codes(request):
    all_judges = list(Judge.objects.order_by("name"))
    missing_email_count = len([judge for judge in all_judges if not judge.email])
    rate_limit_hours = max(1, int(EMAIL_RATE_LIMIT_WINDOW.total_seconds() // 3600))

    tournament_name = TabSettings.get("tournament_name", "your tournament")
    plan = _prepare_judge_code_plan(all_judges, tournament_name, request)
    sendable_by_id = {entry["judge"].id: entry for entry in plan["sendable"]}
    default_selected_ids = set(sendable_by_id.keys())

    if request.method == "POST":
        selected_ids = {
            int(judge_id)
            for judge_id in request.POST.getlist("judge_ids")
            if judge_id.isdigit()
        }
        selected_ids &= default_selected_ids
    else:
        selected_ids = default_selected_ids

    last_sent_map = dict(
        JudgeCodeEmailLog.objects.values("judge_id")
        .annotate(last_sent=Max("sent_at"))
        .values_list("judge_id", "last_sent")
    )

    status_lookup = {}
    reason_lookup = {}
    for judge, reason in plan["skipped_missing_email"]:
        status_lookup[judge.id] = "Missing email"
        reason_lookup[judge.id] = reason
    for judge, reason in plan["skipped_rate_limited"]:
        status_lookup[judge.id] = "Recently emailed"
        reason_lookup[judge.id] = reason
    for judge, reason in plan["skipped_invalid"]:
        status_lookup[judge.id] = "Invalid code"
        reason_lookup[judge.id] = reason
    for judge, reason in plan["skipped_duplicate_email"]:
        status_lookup[judge.id] = "Duplicate email"
        reason_lookup[judge.id] = reason

    judge_rows = []
    for judge in all_judges:
        can_send = judge.id in sendable_by_id
        checked = can_send and judge.id in selected_ids
        status = status_lookup.get(judge.id, "Ready" if can_send else "Not eligible")
        judge_rows.append({
            "judge": judge,
            "email": judge.email or "",
            "ballot_code": judge.ballot_code,
            "can_send": can_send,
            "checked": checked,
            "status": status,
            "reason": reason_lookup.get(judge.id),
            "recent": judge.id in plan["recent_judges"],
            "last_sent": last_sent_map.get(judge.id),
        })

    if request.method == "POST":
        selected_entries = [sendable_by_id[jid] for jid in selected_ids]

        if not selected_entries:
            skipped_total = (
                len(plan["skipped_invalid"]) +
                len(plan["skipped_rate_limited"]) +
                len(plan["skipped_duplicate_email"]) +
                len(plan["skipped_missing_email"])
            )
            reason = " due to rate limiting or invalid data" if skipped_total else ""
            return redirect_and_flash_error(
                request,
                f"No judge codes were sent{reason}.",
            )

        email_requests = [entry["email_request"] for entry in selected_entries]
        log_entries = [entry["log_entry"] for entry in selected_entries]

        try:
            sent = EmailService().send_bulk(email_requests)
        except (EmailServiceError, ImproperlyConfigured) as exc:
            return redirect_and_flash_error(
                request,
                f"Unable to send judge codes: {exc}",
            )

        JudgeCodeEmailLog.objects.bulk_create(log_entries)

        skipped_total = (
            len(plan["skipped_invalid"]) +
            len(plan["skipped_rate_limited"]) +
            len(plan["skipped_duplicate_email"]) +
            len(plan["skipped_missing_email"])
        )
        message = f"Sent ballot codes to {sent} judge{'' if sent == 1 else 's'}."
        if skipped_total:
            message += f" Skipped {skipped_total} due to invalid codes or rate limiting."

        return redirect_and_flash_success(
            request,
            message,
        )

    context = {
        "missing_email_count": missing_email_count,
        "judge_rows": judge_rows,
        "rate_limit_hours": rate_limit_hours,
        "sendable_count": len(sendable_by_id),
        "skipped_invalid": plan["skipped_invalid"],
        "skipped_rate_limited": plan["skipped_rate_limited"],
        "skipped_duplicate_email": plan["skipped_duplicate_email"],
        "skipped_missing_email": plan["skipped_missing_email"],
    }
    return render(request, "tab/send_judge_codes.html", context)
