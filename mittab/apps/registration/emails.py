from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone

from mittab.apps.tab.models import (
    BALLOT_CODE_MAX_LENGTH,
    Judge,
    JudgeCodeEmailLog,
    TabSettings,
    Team,
)
from mittab.libs.email_service import EmailService, EmailServiceError
from mittab.libs.email_views import (
    build_judge_ballot_code_email,
    build_registration_confirmation_email as build_confirmation_email_request,
    build_registration_team_portal_email,
)

from .models import Registration, RegistrationLink


JUDGE_CODE_EMAIL_RATE_LIMIT_WINDOW = timedelta(hours=6)


def _registration_for_email(registration):
    return (
        Registration.objects.select_related("school")
        .prefetch_related(
            Prefetch(
                "teams",
                queryset=Team.objects.select_related(
                    "school", "hybrid_school"
                ).prefetch_related("debaters__school"),
            ),
            Prefetch(
                "judges",
                queryset=Judge.objects.prefetch_related(
                    "schools",
                    "expected_checkins",
                ),
            ),
        )
        .get(pk=registration.pk)
    )


def _team_portal_url(request, team):
    return request.build_absolute_uri(reverse("team_portal", args=[team.team_code]))


def _team_recipient_emails(team):
    emails = []
    seen = set()
    for debater in team.debaters.all():
        email = (debater.email or "").strip()
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        emails.append(email)
    return emails


def build_registration_confirmation_email(registration, request):
    registration = _registration_for_email(registration)
    tournament_name = TabSettings.get("tournament_name", "Tournament")
    edit_url = request.build_absolute_uri(
        reverse("registration_portal_edit", args=[registration.herokunator_code])
    )
    followup_links = list(RegistrationLink.objects.filter(is_active=True))
    return build_confirmation_email_request(
        registration,
        tournament_name,
        edit_url,
        followup_links,
        request,
    )


def send_registration_confirmation_email(registration, request):
    email_request = build_registration_confirmation_email(registration, request)
    return EmailService().send_bulk([email_request])


def build_registration_team_portal_emails(registration, request):
    registration = _registration_for_email(registration)
    tournament_name = TabSettings.get("tournament_name", "Tournament")
    email_requests = []

    for team in registration.teams.all():
        portal_url = _team_portal_url(request, team)
        recipient_emails = _team_recipient_emails(team)
        if not recipient_emails:
            continue
        for email in recipient_emails:
            email_requests.append(
                build_registration_team_portal_email(
                    email,
                    team,
                    tournament_name,
                    portal_url,
                )
            )

    return email_requests


def send_registration_team_portal_emails(registration, request):
    email_requests = build_registration_team_portal_emails(registration, request)
    if not email_requests:
        return 0
    return EmailService().send_bulk(email_requests)


def _build_registration_judge_code_plan(registration, request):
    judge_emails = {
        item["judge_id"]: (item.get("email") or "").strip()
        for item in getattr(registration, "transient_judge_emails", [])
    }
    judge_ids = [judge_id for judge_id, email in judge_emails.items() if email]
    if not judge_ids:
        return []

    judges = {
        judge.pk: judge
        for judge in Judge.objects.filter(pk__in=judge_ids)
        .prefetch_related("expected_checkins")
        .order_by("name")
    }
    recent_judge_ids = set(
        JudgeCodeEmailLog.objects.filter(
            judge_id__in=judge_ids,
            sent_at__gte=timezone.now() - JUDGE_CODE_EMAIL_RATE_LIMIT_WINDOW,
        ).values_list("judge_id", flat=True)
    )
    tournament_name = TabSettings.get("tournament_name", "your tournament")
    portal_search_url = request.build_absolute_uri(reverse("e_ballot_search"))
    entries = []

    for judge_id in judge_ids:
        judge = judges.get(judge_id)
        email = judge_emails[judge_id]
        if not judge or judge_id in recent_judge_ids:
            continue

        try:
            judge.is_valid_ballot_code()
        except ValidationError:
            continue

        if not judge.ballot_code:
            judge.set_unique_ballot_code()
            judge.save(update_fields=["ballot_code"])

        if len(judge.ballot_code or "") > BALLOT_CODE_MAX_LENGTH:
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
            include_registration_confirmation=True,
        )
        entries.append(
            {
                "email_request": email_request,
                "log_entry": JudgeCodeEmailLog(
                    judge=judge,
                    email="",
                    ballot_code=judge.ballot_code,
                ),
            }
        )

    return entries


def send_registration_judge_code_emails(registration, request):
    entries = _build_registration_judge_code_plan(registration, request)
    if not entries:
        return 0

    email_requests = [entry["email_request"] for entry in entries]
    try:
        sent = EmailService().send_bulk(email_requests)
    except EmailServiceError as exc:
        sent_request_ids = {id(email_request) for email_request in exc.sent_requests}
        sent_log_entries = [
            entry["log_entry"]
            for entry in entries
            if id(entry["email_request"]) in sent_request_ids
        ]
        if sent_log_entries:
            JudgeCodeEmailLog.objects.bulk_create(sent_log_entries)
        raise

    JudgeCodeEmailLog.objects.bulk_create([entry["log_entry"] for entry in entries])
    return sent
