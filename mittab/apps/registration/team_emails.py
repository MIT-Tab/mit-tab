"""Shared planning and delivery for registration and manual team code emails."""
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from mittab.apps.tab.models import TabSettings, TeamCodeEmailLog
from mittab.libs.email_service import EmailService, EmailServiceError
from mittab.libs.email_views import build_registration_team_portal_email

TEAM_CODE_EMAIL_RATE_LIMIT_WINDOW = timedelta(hours=6)


def team_recipient_emails(team):
    emails = {}
    for debater in team.debaters.all():
        email = (debater.email or "").strip()
        if email:
            emails.setdefault(email.lower(), email)
    return list(emails.values())


def team_email_snapshot(team, tournament_name):
    """Only track information that affects the email or its recipients."""
    return {
        "name": team.name,
        "team_code": team.team_code,
        "debaters": sorted(debater.name for debater in team.debaters.all()),
        "recipients": sorted(email.lower() for email in team_recipient_emails(team)),
        "tournament_name": tournament_name,
    }


def build_team_code_emails(team, request):
    tournament_name = TabSettings.get("tournament_name", "Tournament")
    if not team.team_code:
        return []
    try:
        portal_url = request.build_absolute_uri(
            reverse("team_portal", args=[team.team_code]),
        )
    except NoReverseMatch:
        return []
    return [
        build_registration_team_portal_email(email, team, tournament_name, portal_url)
        for email in team_recipient_emails(team)
    ]


def prepare_team_code_plan(teams, request):
    """Apply the same team/address cooldown and batch deduplication as judge codes."""
    recent_logs = TeamCodeEmailLog.objects.filter(
        successful=True,
        sent_at__gte=timezone.now() - TEAM_CODE_EMAIL_RATE_LIMIT_WINDOW,
    )
    recent_emails = {
        email.lower() for email in recent_logs.values_list("email", flat=True)
    }
    recent_teams = set(recent_logs.values_list("team_id", flat=True))
    queued_emails = set()
    tournament_name = TabSettings.get("tournament_name", "Tournament")
    entries = []
    for team in teams:
        if team.pk in recent_teams:
            continue
        snapshot = team_email_snapshot(team, tournament_name)
        for email_request in build_team_code_emails(team, request):
            email_lower = email_request.to_address.lower()
            if email_lower in recent_emails or email_lower in queued_emails:
                continue
            entries.append({
                "team": team,
                "email_request": email_request,
                "log_entry": TeamCodeEmailLog(
                    team=team,
                    email=email_request.to_address,
                    team_snapshot=snapshot,
                    email_payload=email_request.snapshot,
                ),
            })
            queued_emails.add(email_lower)
    return entries


def send_team_code_entries(entries, source="manual", service_factory=None):
    if not entries:
        return 0
    email_requests = [entry["email_request"] for entry in entries]
    sent_ids = set()
    error = ""
    try:
        sent = (service_factory or EmailService)().send_bulk(email_requests)
        sent_ids = {id(email_request) for email_request in email_requests}
    except (ImproperlyConfigured, EmailServiceError) as exc:
        error = str(exc)
        sent_ids = {id(item) for item in getattr(exc, "sent_requests", [])}
        raise
    finally:
        logs = []
        for entry in entries:
            log = entry["log_entry"]
            log.source = source
            log.successful = id(entry["email_request"]) in sent_ids
            log.error_message = "" if log.successful else error
            logs.append(log)
        TeamCodeEmailLog.objects.bulk_create(logs)
    return sent
