"""Team code email management and resend eligibility."""
from collections import Counter, defaultdict

from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from mittab.apps.registration.team_emails import (
    build_team_code_emails,
    prepare_team_code_plan,
    send_team_code_entries,
    team_email_snapshot,
    team_recipient_emails,
)
from mittab.apps.tab.helpers import redirect_and_flash_error, redirect_and_flash_success
from mittab.apps.tab.models import TabSettings, Team, TeamCodeEmailLog
from mittab.libs.email_service import EmailServiceError


def team_email_management_context(request):
    teams = list(Team.objects.prefetch_related("debaters").order_by("name"))
    selected_ids = {
        int(value) for value in request.POST.getlist("team_ids") if value.isdigit()
    }
    planned_teams = teams
    if request.method == "POST":
        planned_teams = [team for team in teams if team.pk in selected_ids]
    entries = prepare_team_code_plan(planned_teams, request)
    sendable_counts = Counter(entry["team"].pk for entry in entries)
    sendable_ids = set(sendable_counts)
    logs_by_team = defaultdict(list)
    for log in TeamCodeEmailLog.objects.order_by("-sent_at", "-pk"):
        logs_by_team[log.team_id].append(log)
    tournament_name = TabSettings.get("tournament_name", "Tournament")
    rows = []
    for team in teams:
        logs = logs_by_team[team.pk]
        successful_logs = [log for log in logs if log.successful]
        previews = build_team_code_emails(team, request)
        snapshot = team_email_snapshot(team, tournament_name)
        latest_by_recipient = {}
        for log in successful_logs:
            latest_by_recipient.setdefault(log.email.lower(), log)
        needs_resend = bool(successful_logs) and any(
            email not in latest_by_recipient
            or latest_by_recipient[email].team_snapshot != snapshot
            for email in snapshot["recipients"]
        )
        # A removed recipient also changes what directors should review.
        if successful_logs and successful_logs[0].team_snapshot != snapshot:
            needs_resend = True
        never_received = not successful_logs
        if not team.team_code:
            status = "Missing team code"
        elif not previews:
            status = "Invalid team code" if team_recipient_emails(team) else "Missing email"
        elif needs_resend:
            status = "Needs resend"
        elif logs and not logs[0].successful:
            status = "Failed"
        elif never_received:
            status = "Never sent"
        else:
            status = "Sent"
        can_send = team.pk in sendable_ids
        rows.append({
            "team": team,
            "status": status,
            "needs_resend": needs_resend,
            "never_received": never_received,
            "can_send": can_send,
            "checked": can_send and (
                team.pk in selected_ids if request.method == "POST" else never_received
            ),
            "preview": previews[0] if previews else None,
            "recipients": ", ".join(item.to_address for item in previews),
            "last_log": logs[0] if logs else None,
            "cooldown": bool(previews) and not can_send,
            "sendable_recipients": sendable_counts[team.pk],
        })
    return {
        "rows": rows,
        "sendable_count": len(sendable_ids),
        "selected_entries": entries,
    }


def send_team_code_emails(request, entries):
    if not entries:
        return redirect_and_flash_error(
            request,
            "No team code emails were sent. Check selection, addresses "
            "and the six-hour cooldown.",
            path=reverse("email_management"),
        )
    try:
        sent = send_team_code_entries(entries)
    except (ImproperlyConfigured, EmailServiceError) as exc:
        sent_count = len(getattr(exc, "sent_requests", []))
        return redirect_and_flash_error(
            request,
            f"Unable to send all team codes ({sent_count} sent): {exc}",
            path=reverse("email_management"),
        )
    return redirect_and_flash_success(
        request,
        f"Sent {sent} team code email(s). Unavailable recipients were skipped.",
        path=reverse("email_management"),
    )


def team_code_history_rows():
    return [{
        "type": "Team code",
        "target": log.team_snapshot.get("name", log.team.name),
        "target_url": reverse("view_team", args=[log.team_id]),
        "recipient": log.email,
        "detail": f"{log.source.title()}: " + (
            "Sent successfully" if log.successful else f"Failed: {log.error_message}"
        ),
        "sent_at": log.sent_at,
        "email_payload": log.email_payload,
    } for log in TeamCodeEmailLog.objects.select_related("team").order_by("-sent_at")]
