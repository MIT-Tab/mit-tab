from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from mittab.apps.tab.models import (
    BALLOT_CODE_MAX_LENGTH,
    Debater,
    Judge,
    JudgeCodeEmailLog,
    TabSettings,
    Team,
)
from mittab.libs.email_service import EmailRequest, EmailService, EmailServiceError

from .models import Registration


JUDGE_CODE_EMAIL_RATE_LIMIT_WINDOW = timedelta(hours=6)


def _seed_label(team):
    return dict(Team.SEED_CHOICES).get(team.seed, str(team.seed))


def _debater_status_label(debater):
    return dict(Debater.NOVICE_CHOICES).get(debater.novice_status, "Unknown")


def _format_available_rounds(checkins):
    rounds = sorted(checkin.round_number for checkin in checkins)
    if not rounds:
        return "None selected"
    labels = [
        "Outrounds" if round_number == 0 else f"Round {round_number}"
        for round_number in rounds
    ]
    return ", ".join(labels)


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


def _team_text_lines(team):
    lines = [
        f"- {team.name}",
        f"  School protection: {team.school.name}",
        f"  Seed: {_seed_label(team)}",
        "  Debaters:",
    ]
    for debater in team.debaters.all():
        school_name = debater.school.name if debater.school else "No school"
        apda_id = debater.apda_id if debater.apda_id not in (None, -1) else "None"
        qualified = "yes" if debater.qualified else "no"
        lines.append(
            f"    - {debater.name} "
            f"({school_name}; {_debater_status_label(debater)}; "
            f"APDA ID: {apda_id}; Qualified: {qualified})"
        )
    return lines


def _judge_text_lines(judge, fallback_school):
    schools = ", ".join(s.name for s in judge.schools.all()) or fallback_school
    availability = _format_available_rounds(judge.expected_checkins.all())
    return [
        f"- {judge.name}",
        f"  Experience: {judge.rank}",
        f"  Schools: {schools}",
        f"  Availability: {availability}",
    ]


def _build_text_body(registration, tournament, edit_url):
    lines = [
        f"Hi {registration.school.name},",
        "",
        f"Your registration for {tournament} has been received. "
        "A copy is below for your records.",
        "",
        f"Edit your registration: {edit_url}",
        f"Registration code: {registration.herokunator_code}",
        "",
        "School and contact",
        f"  School: {registration.school.name}",
        f"  Email:  {registration.email}",
        "",
        "Teams",
    ]
    teams = list(registration.teams.all())
    if teams:
        for team in teams:
            lines.extend(_team_text_lines(team))
    else:
        lines.append("- None")

    lines.extend(["", "Judges"])
    judges = list(registration.judges.all())
    if judges:
        for judge in judges:
            lines.extend(_judge_text_lines(judge, registration.school.name))
    else:
        lines.append("- None")

    lines.extend(["", "Thank you,", "Tab Staff"])
    return "\n".join(lines)


def _team_html_lines(team):
    out = [
        "<li style=\"margin-bottom:10px;\">",
        f"<strong>{escape(team.name)}</strong>",
        "<ul style=\"margin:4px 0 0;padding-left:20px;\">",
        f"<li>School protection: {escape(team.school.name)}</li>",
        f"<li>Seed: {escape(_seed_label(team))}</li>",
        "<li>Debaters:<ul style=\"margin:4px 0 0;padding-left:20px;\">",
    ]
    for debater in team.debaters.all():
        school_name = debater.school.name if debater.school else "No school"
        apda_id = debater.apda_id if debater.apda_id not in (None, -1) else "None"
        qualified = "yes" if debater.qualified else "no"
        out.append(
            f"<li>{escape(debater.name)} "
            f"({escape(school_name)}; {escape(_debater_status_label(debater))}; "
            f"APDA ID: {escape(apda_id)}; Qualified: {escape(qualified)})</li>"
        )
    out.extend(["</ul></li>", "</ul>", "</li>"])
    return out


def _judge_html_lines(judge, fallback_school):
    schools = ", ".join(s.name for s in judge.schools.all()) or fallback_school
    availability = _format_available_rounds(judge.expected_checkins.all())
    return [
        "<li style=\"margin-bottom:10px;\">",
        f"<strong>{escape(judge.name)}</strong>",
        "<ul style=\"margin:4px 0 0;padding-left:20px;\">",
        f"<li>Experience: {escape(judge.rank)}</li>",
        f"<li>Schools: {escape(schools)}</li>",
        f"<li>Availability: {escape(availability)}</li>",
        "</ul>",
        "</li>",
    ]


# Inline styles only — many email clients drop <style> blocks. The outer
# table is the standard "responsive centered card" layout that renders
# consistently across Gmail / Outlook / Apple Mail.
_HTML_TEMPLATE = """\
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" \
"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta http-equiv="X-UA-Compatible" content="IE=edge" />
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f6fa;\
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;\
color:#1f2f4a;line-height:1.5;">
<div style="display:none;font-size:0;line-height:0;color:#f4f6fa;\
max-height:0;max-width:0;overflow:hidden;mso-hide:all;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" \
style="background-color:#f4f6fa;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" \
style="max-width:600px;width:100%;background-color:#ffffff;border:1px solid #e3e8ef;\
border-radius:8px;">
<tr><td style="padding:28px 32px;color:#1f2f4a;font-size:15px;line-height:1.55;">
{body}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def _build_html_body_inner(registration, tournament, edit_url):
    parts = [
        f"<p style=\"margin:0 0 12px;\">Hi {escape(registration.school.name)},</p>",
        (
            "<p style=\"margin:0 0 16px;\">Your registration for "
            f"<strong>{escape(tournament)}</strong> has been received. "
            "A copy is below for your records.</p>"
        ),
        (
            "<p style=\"margin:0 0 20px;\">"
            f"<a href=\"{escape(edit_url)}\" "
            "style=\"display:inline-block;background-color:#2a66c4;"
            "color:#ffffff;text-decoration:none;padding:10px 18px;"
            "border-radius:6px;font-weight:600;\">Edit registration</a></p>"
        ),
        (
            "<p style=\"margin:0 0 24px;color:#5b6b85;font-size:14px;\">"
            f"Registration code: <strong>{escape(registration.herokunator_code)}"
            "</strong></p>"
        ),
        (
            "<h2 style=\"margin:24px 0 8px;font-size:16px;color:#1f2f4a;\">"
            "School and contact</h2>"
        ),
        (
            "<p style=\"margin:0 0 8px;\">"
            f"<strong>School:</strong> {escape(registration.school.name)}<br />"
            f"<strong>Email:</strong> {escape(registration.email)}</p>"
        ),
        (
            "<h2 style=\"margin:24px 0 8px;font-size:16px;color:#1f2f4a;\">"
            "Teams</h2>"
        ),
    ]
    teams = list(registration.teams.all())
    if teams:
        parts.append("<ul style=\"margin:0;padding-left:20px;\">")
        for team in teams:
            parts.extend(_team_html_lines(team))
        parts.append("</ul>")
    else:
        parts.append("<p style=\"margin:0;\">None</p>")

    parts.append(
        "<h2 style=\"margin:24px 0 8px;font-size:16px;color:#1f2f4a;\">Judges</h2>"
    )
    judges = list(registration.judges.all())
    if judges:
        parts.append("<ul style=\"margin:0;padding-left:20px;\">")
        for judge in judges:
            parts.extend(_judge_html_lines(judge, registration.school.name))
        parts.append("</ul>")
    else:
        parts.append("<p style=\"margin:0;\">None</p>")

    parts.append(
        "<p style=\"margin:28px 0 0;color:#5b6b85;font-size:14px;\">"
        "Thank you,<br />Tab Staff</p>"
    )
    return "\n".join(parts)


def build_registration_confirmation_email(registration, request):
    registration = _registration_for_email(registration)
    tournament_name = TabSettings.get("tournament_name", "Tournament")
    edit_url = request.build_absolute_uri(
        reverse("registration_portal_edit", args=[registration.herokunator_code])
    )
    subject = f"Registration confirmed for {tournament_name}"
    preheader = (
        f"Code {registration.herokunator_code}. "
        "Edit your registration any time before the tournament."
    )
    text_body = _build_text_body(registration, tournament_name, edit_url)
    inner_html = _build_html_body_inner(registration, tournament_name, edit_url)
    html_body = _HTML_TEMPLATE.format(
        title=escape(subject),
        preheader=escape(preheader),
        body=inner_html,
    )

    return EmailRequest(
        to_address=registration.email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )


def send_registration_confirmation_email(registration, request):
    email_request = build_registration_confirmation_email(registration, request)
    return EmailService().send_bulk([email_request])


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
    subject = f"{tournament_name} Judge Portal and Ballots"
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
        availability = _format_available_rounds(judge.expected_checkins.all())
        text_body = (
            f"Hi {judge.name},\n\n"
            "This email confirms that you have been registered for "
            f"{tournament_name}.\n\n"
            f"Your judge code for {tournament_name} is {judge.ballot_code}.\n"
            f"Your current availability is: {availability}.\n\n"
            f"Open the judge portal to change availability or submit ballots: {portal_url}\n"
            f"You can also search by judge code at {portal_search_url}.\n\n"
            "Thank you,\n"
            "Tab Staff"
        )
        email_request = EmailRequest(
            to_address=email,
            subject=subject,
            text_body=text_body,
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
