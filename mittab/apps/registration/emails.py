from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models import Prefetch, Q
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
)

from .models import (
    Registration,
    RegistrationConfirmationEmailLog,
    RegistrationLink,
)

from .team_emails import (
    build_team_code_emails,
    prepare_team_code_plan,
    send_team_code_entries,
)


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
    try:
        sent = EmailService().send_bulk([email_request])
    except (ImproperlyConfigured, EmailServiceError) as exc:
        RegistrationConfirmationEmailLog.objects.create(
            registration=registration,
            email=email_request.to_address,
            email_payload=email_request.snapshot,
            successful=False,
            error_message=str(exc),
        )
        raise

    RegistrationConfirmationEmailLog.objects.create(
        registration=registration,
        email=email_request.to_address,
        email_payload=email_request.snapshot,
        successful=True,
    )
    return sent


def build_registration_team_portal_emails(registration, request):
    registration = _registration_for_email(registration)
    return [
        email_request
        for team in registration.teams.all()
        for email_request in build_team_code_emails(team, request)
    ]


def send_registration_team_portal_emails(registration, request):
    registration = _registration_for_email(registration)
    entries = prepare_team_code_plan(registration.teams.all(), request)
    return send_team_code_entries(
        entries, source="registration", service_factory=EmailService,
    )


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
    emails_lower = [email.lower() for email in judge_emails.values() if email]
    email_query = Q()
    for email in emails_lower:
        email_query |= Q(email__iexact=email)
    recent_logs = JudgeCodeEmailLog.objects.filter(
        sent_at__gte=timezone.now() - JUDGE_CODE_EMAIL_RATE_LIMIT_WINDOW,
    ).filter(
        Q(judge_id__in=judge_ids) | email_query
    )
    recent_judge_ids = set(recent_logs.values_list("judge_id", flat=True))
    recent_email_counts = {}
    for email in recent_logs.values_list("email", flat=True):
        email_lower = email.lower()
        recent_email_counts[email_lower] = (
            recent_email_counts.get(email_lower, 0) + 1
        )
    batch_email_counts = {}

    def email_rate_limited(email):
        return recent_email_counts.get(email.lower(), 0) >= 1

    def email_duplicate_in_batch(email):
        return batch_email_counts.get(email.lower(), 0) >= 1

    def record_email_queued(email):
        email_lower = email.lower()
        recent_email_counts[email_lower] = (
            recent_email_counts.get(email_lower, 0) + 1
        )
        batch_email_counts[email_lower] = batch_email_counts.get(
            email_lower,
            0,
        ) + 1

    tournament_name = TabSettings.get("tournament_name", "your tournament")
    portal_search_url = request.build_absolute_uri(reverse("e_ballot_search"))
    entries = []

    for judge_id in judge_ids:
        judge = judges.get(judge_id)
        email = judge_emails[judge_id]
        if (
            not judge
            or judge_id in recent_judge_ids
            or email_rate_limited(email)
            or email_duplicate_in_batch(email)
        ):
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
                    email=email,
                    ballot_code=judge.ballot_code,
                    source=JudgeCodeEmailLog.SOURCE_REGISTRATION,
                    email_payload=email_request.snapshot,
                ),
            }
        )
        record_email_queued(email)

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
