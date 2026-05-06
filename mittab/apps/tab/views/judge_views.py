from collections import Counter
from datetime import timedelta

from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models import Q, Max
from django.utils import timezone

from mittab.apps.tab.auth_roles import (
    CAP_CHECKINS,
    CAP_DATA_ENTRY,
    CAP_VIEW_SCRATCHES,
    user_has_staff_capability,
)
from mittab.apps.tab.forms import JudgeForm, ScratchForm
from mittab.apps.tab.helpers import redirect_and_flash_error, redirect_and_flash_success
from mittab.apps.tab.models import *
from mittab.libs.errors import *
from mittab.libs.tab_logic import TabFlags
from mittab.libs.email_service import EmailService, EmailServiceError
from mittab.libs.email_views import (
    build_judge_ballot_code_email,
    build_written_rfd_email,
)


EMAIL_RATE_LIMIT_WINDOW = timedelta(hours=6)
EMAILS_PER_ADDRESS_PER_WINDOW = 1
EMAILS_PER_ADDRESS_PER_BATCH = 1


def _format_available_rounds(expected_checkins):
    rounds = sorted(checkin.round_number for checkin in expected_checkins)
    if not rounds:
        return "None selected"
    labels = [
        "Outrounds" if round_number == 0 else f"Round {round_number}"
        for round_number in rounds
    ]
    return ", ".join(labels)


def _prepare_judge_code_plan(judges, tournament_name, request):
    portal_search_url = request.build_absolute_uri(reverse("e_ballot_search"))
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
            skipped_duplicate_email.append(
                (judge, "Email already queued in this batch")
            )
            continue

        if not judge.ballot_code:
            judge.set_unique_ballot_code()
            judge.save(update_fields=["ballot_code"])

        if len(judge.ballot_code or "") > BALLOT_CODE_MAX_LENGTH:
            skipped_invalid.append(
                (judge, f"Code exceeds {BALLOT_CODE_MAX_LENGTH} characters")
            )
            continue

        portal_url = request.build_absolute_uri(
            reverse("judge_portal", args=[judge.ballot_code])
        )
        email_request = build_judge_ballot_code_email(
            email,
            judge,
            tournament_name,
            portal_url,
            portal_search_url,
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
            "ballot_url": portal_url,
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


def _completed_rounds_with_written_rfds():
    completed_victors = (
        Round.GOV,
        Round.OPP,
        Round.GOV_VIA_FORFEIT,
        Round.OPP_VIA_FORFEIT,
    )
    first_round = int(TabSettings.get("written_rfd_first_round", -1) or -1)
    if first_round < 1:
        return Round.objects.none()

    return (
        Round.objects.filter(
            round_number__gte=first_round,
            victor__in=completed_victors,
            roundstats__isnull=False,
        )
        .select_related("gov_team", "opp_team", "chair")
        .prefetch_related("gov_team__debaters", "opp_team__debaters")
        .distinct()
        .order_by("-round_number", "gov_team__name", "opp_team__name")
    )


def _recipient_emails_for_round(round_obj):
    emails = []
    seen = set()
    for team in (round_obj.gov_team, round_obj.opp_team):
        for debater in team.debaters.all():
            email = (debater.email or "").strip()
            if not email:
                continue
            email_key = email.lower()
            if email_key in seen:
                continue
            seen.add(email_key)
            emails.append(email)
    return emails


def _prepare_written_rfd_plan(rounds, tournament_name):
    sendable = []
    skipped_missing_rfd = []
    skipped_missing_email = []

    for round_obj in rounds:
        recipient_emails = _recipient_emails_for_round(round_obj)
        if not recipient_emails:
            skipped_missing_email.append((round_obj, "No debater emails on file"))
            continue

        rfd_text = (round_obj.rfd or "").strip()
        if not rfd_text:
            skipped_missing_rfd.append((round_obj, "No written RFD entered"))
            continue

        winner = round_obj.winner
        winner_name = winner.display if winner else round_obj.get_victor_display()
        judge_name = round_obj.chair.name if round_obj.chair else "Unknown"
        email_requests = [
            build_written_rfd_email(
                email,
                tournament_name,
                round_obj,
                winner_name,
                judge_name,
                rfd_text,
            )
            for email in recipient_emails
        ]
        log_entries = [
            WrittenRFDEmailLog(round=round_obj, email=email)
            for email in recipient_emails
        ]
        sendable.append({
            "round": round_obj,
            "recipient_emails": recipient_emails,
            "email_requests": email_requests,
            "log_entries": log_entries,
        })

    return {
        "sendable": sendable,
        "skipped_missing_rfd": skipped_missing_rfd,
        "skipped_missing_email": skipped_missing_email,
    }


def view_judges(request):
    # Get a list of (id,school_name) tuples
    current_round = TabSettings.objects.get(key="cur_round").value - 1
    include_checkins = user_has_staff_capability(request.user, CAP_CHECKINS)
    checked_in_judges = set()
    checked_in_judges_next = set()
    if include_checkins:
        checkins = CheckIn.objects.filter(round_number=current_round)
        checkins_next = CheckIn.objects.filter(round_number=current_round + 1)
        checked_in_judges = set([c.judge for c in checkins])
        checked_in_judges_next = set([c.judge for c in checkins_next])

    def flags(judge):
        result = 0
        if include_checkins:
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

    all_flags = [[
        TabFlags.LOW_RANKED_JUDGE,
        TabFlags.MID_RANKED_JUDGE,
        TabFlags.HIGH_RANKED_JUDGE,
    ]]
    if include_checkins:
        all_flags.insert(0, [
            TabFlags.JUDGE_CHECKED_IN_CUR,
            TabFlags.JUDGE_NOT_CHECKED_IN_CUR,
            TabFlags.JUDGE_CHECKED_IN_NEXT,
            TabFlags.JUDGE_NOT_CHECKED_IN_NEXT,
        ])
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
        form = JudgeForm(
            request.POST,
            instance=judge,
            allow_checkins=user_has_staff_capability(request.user, CAP_CHECKINS),
        )
        if form.is_valid():
            try:
                form.save(actor=request.user)
            except (ValueError, ValidationError):
                return redirect_and_flash_error(
                    request, "Judge information cannot be validated")
            updated_name = form.cleaned_data["name"]
            return redirect_and_flash_success(
                request, f"Judge {updated_name} updated successfully")
    else:
        form = JudgeForm(
            instance=judge,
            allow_checkins=user_has_staff_capability(request.user, CAP_CHECKINS),
        )
        judging_rounds = list(Round.objects.filter(judges=judge).select_related(
            "gov_team", "opp_team", "room"))
    base_url = f"/judge/{judge_id}/"
    scratch_url = f"{base_url}scratches/view/"
    links = []
    if user_has_staff_capability(request.user, CAP_DATA_ENTRY):
        links.append((f"/judge/{judge_id}/delete/", "Delete"))
    if user_has_staff_capability(request.user, CAP_VIEW_SCRATCHES):
        links.append((scratch_url, f"Scratches for {judge.name}"))
    return render(
        request, "tab/judge_detail.html", {
            "form": form,
            "links": links,
            "judge_rounds": judging_rounds,
            "audit_events": judge.audit_events.all(),
            "judge_obj": judge,
            "title": f"Viewing Judge: {judge.name}"
        })


def enter_judge(request):
    if request.method == "POST":
        form = JudgeForm(request.POST)
        if form.is_valid():
            try:
                form.save(actor=request.user)
            except (ValueError, ValidationError):
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


def delete_judge(request, judge_id):
    if not user_has_staff_capability(request.user, CAP_DATA_ENTRY):
        return redirect_and_flash_error(request, "You cannot delete judges", path="/403/")

    try:
        judge_id = int(judge_id)
        judge = Judge.objects.get(pk=judge_id)
        judge.delete()
    except Judge.DoesNotExist:
        return redirect_and_flash_error(request, "That judge does not exist")
    except Exception as exc:
        return redirect_and_flash_error(request, str(exc))
    return redirect_and_flash_success(request,
                                      "Judge deleted successfully",
                                      path="/")


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
                form.save(actor=request.user)
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
            "forms": list(zip(forms, [None] * len(forms), [None] * len(forms))),
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
                form.save(actor=request.user)
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
    read_only = not request.user.is_superuser
    delete_links = [None] * len(scratches)
    if not read_only:
        delete_links = [
            f"/judge/{judge_id}/scratches/delete/{scratches[i].id}"
            for i in range(len(scratches))
        ]
    metadata = [
        {
            "created_by": scratch.created_by_display,
            "created_at": scratch.created_at,
            "audit_events": scratch.audit_events.all(),
        }
        for scratch in scratches
    ]
    links = [(f"/judge/{judge_id}/scratches/add/1/", "Add Scratch")]

    return render(
        request, "common/data_entry_multiple.html", {
            "forms": list(zip(forms, delete_links, metadata)),
            "data_type": "Scratch",
            "links": links,
            "read_only": read_only,
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


def _judge_email_management_context(request):
    all_judges = list(
        Judge.objects.prefetch_related("expected_checkins").order_by("name")
    )
    missing_email_count = len([judge for judge in all_judges if not judge.email])
    rate_limit_hours = max(1, int(EMAIL_RATE_LIMIT_WINDOW.total_seconds() // 3600))
    tournament_name = TabSettings.get("tournament_name", "your tournament")
    plan = _prepare_judge_code_plan(all_judges, tournament_name, request)
    sendable_by_id = {entry["judge"].id: entry for entry in plan["sendable"]}
    emailed_judge_ids = set(
        JudgeCodeEmailLog.objects.values_list("judge_id", flat=True)
    )
    default_selected_ids = [
        judge_id for judge_id in sendable_by_id.keys()
        if judge_id not in emailed_judge_ids
    ]

    if request.method == "POST":
        selected_ids = []
        seen_ids = set()
        for judge_id in request.POST.getlist("judge_ids"):
            if not judge_id.isdigit():
                continue

            parsed_id = int(judge_id)
            if parsed_id not in sendable_by_id or parsed_id in seen_ids:
                continue

            selected_ids.append(parsed_id)
            seen_ids.add(parsed_id)
    else:
        selected_ids = default_selected_ids
    selected_id_set = set(selected_ids)

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
        checked = can_send and judge.id in selected_id_set
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
            "never_received": judge.id not in emailed_judge_ids,
            "last_sent": last_sent_map.get(judge.id),
        })

    return {
        "missing_email_count": missing_email_count,
        "rows": judge_rows,
        "rate_limit_hours": rate_limit_hours,
        "sendable_count": len(sendable_by_id),
        "never_received_sendable_count": len(default_selected_ids),
        "skipped_invalid": plan["skipped_invalid"],
        "skipped_rate_limited": plan["skipped_rate_limited"],
        "skipped_duplicate_email": plan["skipped_duplicate_email"],
        "skipped_missing_email": plan["skipped_missing_email"],
        "selected_entries": [sendable_by_id[jid] for jid in selected_ids],
    }


def _send_judge_code_emails(request, selected_entries, judge_context):
    if not selected_entries:
        skipped_total = (
            len(judge_context["skipped_invalid"]) +
            len(judge_context["skipped_rate_limited"]) +
            len(judge_context["skipped_duplicate_email"]) +
            len(judge_context["skipped_missing_email"])
        )
        reason = " due to rate limiting or invalid data" if skipped_total else ""
        return redirect_and_flash_error(
            request,
            f"No judge codes were sent{reason}.",
            path=reverse("email_management"),
        )

    email_requests = [entry["email_request"] for entry in selected_entries]
    log_entries = [entry["log_entry"] for entry in selected_entries]

    try:
        sent = EmailService().send_bulk(email_requests)
    except ImproperlyConfigured as exc:
        return redirect_and_flash_error(
            request,
            f"Unable to send judge codes: {exc}",
            path=reverse("email_management"),
        )
    except EmailServiceError as exc:
        sent_request_ids = {
            id(email_request) for email_request in exc.sent_requests
        }
        sent_log_entries = [
            entry["log_entry"]
            for entry in selected_entries
            if id(entry["email_request"]) in sent_request_ids
        ]
        if sent_log_entries:
            JudgeCodeEmailLog.objects.bulk_create(sent_log_entries)

        partial_message = ""
        if sent_log_entries:
            sent_count = len(sent_log_entries)
            partial_message = (
                f" after sending {sent_count} judge code"
                f"{'' if sent_count == 1 else 's'}"
            )

        return redirect_and_flash_error(
            request,
            f"Unable to send judge codes{partial_message}: {exc}",
            path=reverse("email_management"),
        )

    JudgeCodeEmailLog.objects.bulk_create(log_entries)

    skipped_total = (
        len(judge_context["skipped_invalid"]) +
        len(judge_context["skipped_rate_limited"]) +
        len(judge_context["skipped_duplicate_email"]) +
        len(judge_context["skipped_missing_email"])
    )
    message = f"Sent judge portal links to {sent} judge{'' if sent == 1 else 's'}."
    if skipped_total:
        message += (
            f" Skipped {skipped_total} due to invalid codes or rate limiting."
        )

    return redirect_and_flash_success(
        request,
        message,
        path=reverse("email_management"),
    )


def _written_rfd_email_management_context(request):
    rounds = list(_completed_rounds_with_written_rfds())
    tournament_name = TabSettings.get("tournament_name", "your tournament")
    plan = _prepare_written_rfd_plan(rounds, tournament_name)
    sendable_by_id = {entry["round"].id: entry for entry in plan["sendable"]}

    emailed_round_ids = set(
        WrittenRFDEmailLog.objects.values_list("round_id", flat=True)
    )
    default_selected_ids = [
        round_id for round_id in sendable_by_id.keys()
        if round_id not in emailed_round_ids
    ]

    if request.method == "POST":
        selected_ids = []
        seen_ids = set()
        for round_id in request.POST.getlist("round_ids"):
            if not round_id.isdigit():
                continue

            parsed_id = int(round_id)
            if parsed_id not in sendable_by_id or parsed_id in seen_ids:
                continue

            selected_ids.append(parsed_id)
            seen_ids.add(parsed_id)
    else:
        selected_ids = default_selected_ids
    selected_id_set = set(selected_ids)

    last_sent_map = dict(
        WrittenRFDEmailLog.objects.values("round_id")
        .annotate(last_sent=Max("sent_at"))
        .values_list("round_id", "last_sent")
    )

    status_lookup = {}
    reason_lookup = {}
    for round_obj, reason in plan["skipped_missing_rfd"]:
        status_lookup[round_obj.id] = "Missing RFD"
        reason_lookup[round_obj.id] = reason
    for round_obj, reason in plan["skipped_missing_email"]:
        status_lookup[round_obj.id] = "Missing email"
        reason_lookup[round_obj.id] = reason

    round_rows = []
    for round_obj in rounds:
        can_send = round_obj.id in sendable_by_id
        checked = can_send and round_obj.id in selected_id_set
        recipient_count = (
            len(sendable_by_id[round_obj.id]["recipient_emails"])
            if can_send else len(_recipient_emails_for_round(round_obj))
        )
        winner = round_obj.winner
        round_rows.append({
            "round": round_obj,
            "winner": winner.display if winner else round_obj.get_victor_display(),
            "recipient_count": recipient_count,
            "can_send": can_send,
            "checked": checked,
            "status": status_lookup.get(
                round_obj.id,
                "Ready" if can_send else "Not eligible",
            ),
            "reason": reason_lookup.get(round_obj.id),
            "never_received": round_obj.id not in emailed_round_ids,
            "last_sent": last_sent_map.get(round_obj.id),
        })

    return {
        "rows": round_rows,
        "sendable_count": len(sendable_by_id),
        "never_received_sendable_count": len(default_selected_ids),
        "skipped_missing_rfd": plan["skipped_missing_rfd"],
        "skipped_missing_email": plan["skipped_missing_email"],
        "selected_entries": [
            sendable_by_id[round_id] for round_id in selected_ids
        ],
    }


def _send_written_rfd_emails(request, selected_entries):
    if not selected_entries:
        return redirect_and_flash_error(
            request,
            "No written RFD emails were sent.",
            path=reverse("email_management"),
        )

    email_requests = [
        email_request
        for entry in selected_entries
        for email_request in entry["email_requests"]
    ]
    log_entries_by_request_id = {
        id(email_request): log_entry
        for entry in selected_entries
        for email_request, log_entry in zip(
            entry["email_requests"], entry["log_entries"]
        )
    }

    try:
        sent = EmailService().send_bulk(email_requests)
    except ImproperlyConfigured as exc:
        return redirect_and_flash_error(
            request,
            f"Unable to send written RFDs: {exc}",
            path=reverse("email_management"),
        )
    except EmailServiceError as exc:
        sent_log_entries = [
            log_entries_by_request_id[id(email_request)]
            for email_request in exc.sent_requests
            if id(email_request) in log_entries_by_request_id
        ]
        if sent_log_entries:
            WrittenRFDEmailLog.objects.bulk_create(sent_log_entries)

        partial_message = ""
        if sent_log_entries:
            sent_count = len(sent_log_entries)
            partial_message = (
                f" after sending {sent_count} written RFD email"
                f"{'' if sent_count == 1 else 's'}"
            )

        return redirect_and_flash_error(
            request,
            f"Unable to send written RFDs{partial_message}: {exc}",
            path=reverse("email_management"),
        )

    WrittenRFDEmailLog.objects.bulk_create(
        [
            log_entries_by_request_id[id(email_request)]
            for email_request in email_requests
        ]
    )

    return redirect_and_flash_success(
        request,
        f"Sent {sent} written RFD email{'' if sent == 1 else 's'}.",
        path=reverse("email_management"),
    )


def _judge_code_history_rows():
    logs = (
        JudgeCodeEmailLog.objects.select_related("judge")
        .order_by("-sent_at")
    )
    rows = []
    for log in logs:
        rows.append({
            "type": "Judge code",
            "target": log.judge.name,
            "target_url": reverse("view_judge", args=[log.judge_id]),
            "recipient": log.email or "Registration judge email",
            "detail": f"Ballot code {log.ballot_code}",
            "sent_at": log.sent_at,
        })
    return rows


def _written_rfd_history_rows():
    logs = (
        WrittenRFDEmailLog.objects.select_related(
            "round",
            "round__gov_team",
            "round__opp_team",
        )
        .order_by("-sent_at")
    )
    rows = []
    for log in logs:
        round_obj = log.round
        rows.append({
            "type": "Written RFD",
            "target": f"Round {round_obj.round_number}",
            "target_url": "",
            "recipient": log.email,
            "detail": (
                f"{round_obj.gov_team.display} vs "
                f"{round_obj.opp_team.display}"
            ),
            "sent_at": log.sent_at,
        })
    return rows


def _email_history_rows():
    rows = _judge_code_history_rows() + _written_rfd_history_rows()
    return sorted(rows, key=lambda row: row["sent_at"], reverse=True)


def email_management(request):
    judge_context = _judge_email_management_context(request)
    rfd_context = _written_rfd_email_management_context(request)

    if request.method == "POST":
        action = request.POST.get("email_action")
        if action == "written_rfds":
            return _send_written_rfd_emails(
                request,
                rfd_context["selected_entries"],
            )
        return _send_judge_code_emails(
            request,
            judge_context["selected_entries"],
            judge_context,
        )

    return render(
        request,
        "tab/email_management.html",
        {
            "judge_email": judge_context,
            "written_rfd_email": rfd_context,
            "history_rows": _email_history_rows(),
        },
    )
