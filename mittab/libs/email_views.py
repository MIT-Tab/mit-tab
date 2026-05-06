from django.template.loader import render_to_string
from django.urls import reverse

from mittab.apps.tab.models import Debater, Team
from mittab.libs.email_service import EmailRequest


def render_html_email(template_name, subject, preheader, context):
    email_context = {
        **context,
        "subject": subject,
        "preheader": preheader,
        "sender_name": "MIT-TAB",
    }
    return render_to_string(template_name, email_context).strip()


def build_email_request(
        to_address,
        subject,
        text_body,
        template_name,
        preheader,
        context):
    return EmailRequest(
        to_address=to_address,
        subject=subject,
        text_body=text_body,
        html_body=render_html_email(
            template_name,
            subject,
            preheader,
            context,
        ),
    )


def _team_portal_url(request, team):
    return request.build_absolute_uri(reverse("team_portal", args=[team.team_code]))


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


def _team_text_lines(team, request):
    lines = [
        f"- {team.name}",
        f"  School protection: {team.school.name}",
        f"  Seed: {_seed_label(team)}",
        f"  Team code: {team.team_code}",
        f"  Team portal: {_team_portal_url(request, team)}",
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


def _registration_team_context(team, request):
    return {
        "name": team.name,
        "school_name": team.school.name,
        "seed": _seed_label(team),
        "team_code": team.team_code,
        "portal_url": _team_portal_url(request, team),
        "debaters": [
            {
                "name": debater.name,
                "school_name": (
                    debater.school.name if debater.school else "No school"
                ),
                "status": _debater_status_label(debater),
                "apda_id": (
                    debater.apda_id
                    if debater.apda_id not in (None, -1)
                    else "None"
                ),
                "qualified": "yes" if debater.qualified else "no",
            }
            for debater in team.debaters.all()
        ],
    }


def _registration_judge_context(judge, fallback_school):
    return {
        "name": judge.name,
        "rank": judge.rank,
        "schools": ", ".join(s.name for s in judge.schools.all())
        or fallback_school,
        "availability": _format_available_rounds(judge.expected_checkins.all()),
    }


def _registration_text_body(
        registration,
        tournament,
        edit_url,
        followup_links,
        request):
    lines = [
        f"Hi {registration.school.name},",
        "",
        f"Your registration for {tournament} has been received. "
        "A summary of the teams and judges you registered is included "
        "below for your records.",
        "",
        f"Edit your registration any time before the tournament: {edit_url}",
        f"Registration code: {registration.herokunator_code}",
        "",
    ]
    if followup_links:
        lines.append("A few more things to complete:")
        for link in followup_links:
            if link.description:
                lines.append(f"- {link.title}: {link.url} ({link.description})")
            else:
                lines.append(f"- {link.title}: {link.url}")
        lines.append("")
    lines.extend([
        "School and contact",
        f"  School: {registration.school.name}",
        f"  Email:  {registration.email}",
        "",
        "Teams",
    ])
    teams = list(registration.teams.all())
    if teams:
        for team in teams:
            lines.extend(_team_text_lines(team, request))
    else:
        lines.append("  None")

    lines.extend(["", "Judges"])
    judges = list(registration.judges.all())
    if judges:
        for judge in judges:
            lines.extend(_judge_text_lines(judge, registration.school.name))
    else:
        lines.append("  None")

    lines.extend(["", "Thanks,", "The Tab Team"])
    return "\n".join(lines)


def build_registration_confirmation_email(
        registration,
        tournament_name,
        edit_url,
        followup_links,
        request):
    subject = f"Registration confirmed: {tournament_name}"
    preheader = (
        f"Confirmation code {registration.herokunator_code}. "
        "You can edit your registration any time before the tournament."
    )
    text_body = _registration_text_body(
        registration,
        tournament_name,
        edit_url,
        followup_links,
        request,
    )
    context = {
        "school_name": registration.school.name,
        "contact_email": registration.email,
        "tournament_name": tournament_name,
        "edit_url": edit_url,
        "registration_code": registration.herokunator_code,
        "followup_links": followup_links,
        "teams": [
            _registration_team_context(team, request)
            for team in registration.teams.all()
        ],
        "judges": [
            _registration_judge_context(judge, registration.school.name)
            for judge in registration.judges.all()
        ],
    }
    return build_email_request(
        registration.email,
        subject,
        text_body,
        "emails/registration_confirmation.html",
        preheader,
        context,
    )


def build_registration_team_portal_email(
        to_address,
        team,
        tournament_name,
        portal_url):
    subject = f"Your team portal for {tournament_name}"
    debater_names = ", ".join(debater.name for debater in team.debaters.all())
    text_body = (
        "Hello,\n\n"
        f"Your team has been registered for {tournament_name}. "
        "You can open your team portal at the link below to manage "
        "team-specific actions, including judge scratches when they "
        "are open.\n\n"
        f"Team: {team.name}\n"
        f"Debaters: {debater_names}\n"
        f"Team code: {team.team_code}\n"
        f"Team portal: {portal_url}\n\n"
        "Thanks,\n"
        "The Tab Team"
    )
    context = {
        "team_name": team.name,
        "tournament_name": tournament_name,
        "debater_names": debater_names,
        "team_code": team.team_code,
        "portal_url": portal_url,
    }
    return build_email_request(
        to_address,
        subject,
        text_body,
        "emails/team_portal.html",
        f"Team code {team.team_code}. Open your team portal for tournament actions.",
        context,
    )


def build_judge_ballot_code_email(
        to_address,
        judge,
        tournament_name,
        portal_url,
        portal_search_url,
        include_registration_confirmation=False):
    subject = f"Your judge code for {tournament_name}"
    confirmation_text = ""
    if include_registration_confirmation:
        confirmation_text = (
            f"You have been registered as a judge for {tournament_name}.\n\n"
        )
    availability = _format_available_rounds(judge.expected_checkins.all())
    text_body = (
        f"Hi {judge.name},\n\n"
        f"{confirmation_text}"
        f"Your judge code for {tournament_name} is: {judge.ballot_code}\n"
        f"Your current availability is: {availability}.\n\n"
        "Use this code to open the judge portal, update availability, "
        f"and submit ballots at {portal_url}. You can also search by "
        f"judge code at {portal_search_url}.\n\n"
        "Thanks,\n"
        "The Tab Team"
    )
    context = {
        "judge_name": judge.name,
        "tournament_name": tournament_name,
        "ballot_code": judge.ballot_code,
        "availability": availability,
        "portal_url": portal_url,
        "portal_search_url": portal_search_url,
        "include_registration_confirmation": include_registration_confirmation,
    }
    return build_email_request(
        to_address,
        subject,
        text_body,
        "emails/judge_ballot_code.html",
        f"Your judge code for {tournament_name} is {judge.ballot_code}.",
        context,
    )


def build_written_rfd_email(
        to_address,
        tournament_name,
        round_obj,
        winner_name,
        judge_name,
        rfd_text):
    subject = f"Round {round_obj.round_number} decision for {tournament_name}"
    text_body = (
        "Hello,\n\n"
        f"The judge for your Round {round_obj.round_number} debate at "
        f"{tournament_name} has submitted a written reason for decision. "
        "It is included below.\n\n"
        f"Tournament: {tournament_name}\n"
        f"Round: {round_obj.round_number}\n"
        f"Government: {round_obj.gov_team.display}\n"
        f"Opposition: {round_obj.opp_team.display}\n"
        f"Winner: {winner_name}\n"
        f"Judge: {judge_name}\n\n"
        "Reason for decision:\n"
        f"{rfd_text}\n\n"
        "Thanks,\n"
        "The Tab Team"
    )
    context = {
        "tournament_name": tournament_name,
        "round_number": round_obj.round_number,
        "gov_team": round_obj.gov_team.display,
        "opp_team": round_obj.opp_team.display,
        "winner_name": winner_name,
        "judge_name": judge_name,
        "rfd_text": rfd_text,
    }
    return build_email_request(
        to_address,
        subject,
        text_body,
        "emails/written_rfd.html",
        (
            f"Written reason for decision from {judge_name} for "
            f"Round {round_obj.round_number}."
        ),
        context,
    )


def build_staff_invite_email(to_address, username, invite_url):
    subject = "Your invitation to join MIT-TAB"
    text_body = (
        "Hello,\n\n"
        "You have been invited to join the MIT-TAB staff site as a "
        "tournament administrator.\n\n"
        f"Username: {username}\n\n"
        "Use the link below to set your password and finish creating "
        f"your account:\n{invite_url}\n\n"
        "If you were not expecting this invitation, you can safely "
        "ignore this email.\n\n"
        "Thanks,\n"
        "The Tab Team"
    )
    context = {
        "username": username,
        "invite_url": invite_url,
    }
    return build_email_request(
        to_address,
        subject,
        text_body,
        "emails/staff_invite.html",
        "Set your password to finish creating your MIT-TAB staff account.",
        context,
    )
