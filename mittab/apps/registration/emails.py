from django.db.models import Prefetch
from django.urls import reverse
from django.utils.html import escape

from mittab.apps.tab.models import Debater, Judge, TabSettings, Team
from mittab.libs.email_service import EmailRequest, EmailService

from .models import Registration


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
                queryset=Judge.objects.prefetch_related("schools", "checkin_set"),
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
    subject = f"{tournament_name} Registration Confirmation"

    text_lines = [
        f"Your registration for {tournament_name} has been received.",
        "",
        f"Registration code: {registration.herokunator_code}",
        f"Edit link: {edit_url}",
        "",
        "School and contact",
        f"School: {registration.school.name}",
        f"Email: {registration.email}",
        "",
        "Teams",
    ]
    html_lines = [
        (
            f"<p>Your registration for <strong>{escape(tournament_name)}</strong> "
            "has been received.</p>"
        ),
        "<h3>Registration access</h3>",
        "<ul>",
        (
            "<li><strong>Registration code:</strong> "
            f"{escape(registration.herokunator_code)}</li>"
        ),
        (
            f'<li><strong>Edit link:</strong> <a href="{escape(edit_url)}">'
            f"{escape(edit_url)}</a></li>"
        ),
        "</ul>",
        "<h3>School and contact</h3>",
        "<ul>",
        f"<li><strong>School:</strong> {escape(registration.school.name)}</li>",
        f"<li><strong>Email:</strong> {escape(registration.email)}</li>",
        "</ul>",
        "<h3>Teams</h3>",
    ]

    teams = list(registration.teams.all())
    if teams:
        html_lines.append("<ul>")
        for team in teams:
            text_lines.extend(
                [
                    f"- {team.name}",
                    f"  School protection: {team.school.name}",
                    f"  Seed: {_seed_label(team)}",
                    "  Debaters:",
                ]
            )
            html_lines.extend(
                [
                    "<li>",
                    f"<strong>{escape(team.name)}</strong>",
                    "<ul>",
                    f"<li>School protection: {escape(team.school.name)}</li>",
                    f"<li>Seed: {escape(_seed_label(team))}</li>",
                    "<li>Debaters:<ul>",
                ]
            )
            for debater in team.debaters.all():
                school_name = debater.school.name if debater.school else "No school"
                apda_id = (
                    debater.apda_id
                    if debater.apda_id not in (None, -1)
                    else "None"
                )
                qualified = "yes" if debater.qualified else "no"
                text_lines.append(
                    f"    - {debater.name} ({school_name}; "
                    f"{_debater_status_label(debater)}; APDA ID: {apda_id}; "
                    f"Qualified: {qualified})"
                )
                html_lines.append(
                    f"<li>{escape(debater.name)} ({escape(school_name)}; "
                    f"{escape(_debater_status_label(debater))}; "
                    f"APDA ID: {escape(apda_id)}; "
                    f"Qualified: {escape(qualified)})</li>"
                )
            html_lines.extend(["</ul></li>", "</ul>", "</li>"])
        html_lines.append("</ul>")
    else:
        text_lines.append("- None")
        html_lines.append("<p>None</p>")

    text_lines.extend(["", "Judges"])
    html_lines.append("<h3>Judges</h3>")
    judges = list(registration.judges.all())
    if judges:
        html_lines.append("<ul>")
        for judge in judges:
            schools = (
                ", ".join(school.name for school in judge.schools.all())
                or registration.school.name
            )
            availability = _format_available_rounds(judge.checkin_set.all())
            text_lines.extend(
                [
                    f"- {judge.name}",
                    f"  Email: {judge.email}",
                    f"  Experience: {judge.rank}",
                    f"  Schools: {schools}",
                    f"  Availability: {availability}",
                ]
            )
            html_lines.extend(
                [
                    "<li>",
                    f"<strong>{escape(judge.name)}</strong>",
                    "<ul>",
                    f"<li>Email: {escape(judge.email or '')}</li>",
                    f"<li>Experience: {escape(judge.rank)}</li>",
                    f"<li>Schools: {escape(schools)}</li>",
                    f"<li>Availability: {escape(availability)}</li>",
                    "</ul>",
                    "</li>",
                ]
            )
        html_lines.append("</ul>")
    else:
        text_lines.append("- None")
        html_lines.append("<p>None</p>")

    text_lines.extend(["", "Thank you,", "Tab Staff"])
    html_lines.append("<p>Thank you,<br>Tab Staff</p>")

    return EmailRequest(
        to_address=registration.email,
        subject=subject,
        text_body="\n".join(text_lines),
        html_body="\n".join(html_lines),
    )


def send_registration_confirmation_email(registration, request):
    email_request = build_registration_confirmation_email(registration, request)
    return EmailService().send_bulk([email_request])
