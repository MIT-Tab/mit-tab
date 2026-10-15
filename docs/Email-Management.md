# Email Management

Full tab staff can use **Admin > Email Management** to preview and send team
codes, judge codes, registration confirmations, and written RFDs. Choose an email
type, open its HTML preview, select recipients, and send. Previews open in a
dialog that must be dismissed to return to the table. Team and RFD previews list
all recipients and show the shared email once. **Sent Email
History** shows recorded attempts and previews the saved content, including staff
invitations. A current preview describes what would be sent now; a recorded
preview preserves what was submitted to the email service at that time. Successful
means the service accepted the send, rather than proof that it reached an inbox.

Team registration emails include the team name, debater names, team code, and
portal link. They go to each distinct nonempty debater email address, rather than
the school's registration contact. Registration submission sends these emails
automatically. Teams entered manually can be emailed from Email Management.

**Needs resend** appears when the team name, code, debater names or membership,
recipient addresses, or tournament name differs from the successful email
snapshot. Changes to seeds, check-ins, and other information absent from the email
do not trigger this flag. Changes are detected regardless of whether they were
made in the staff roster forms, registration portal, import, or admin interface.
Manual roster edits do not automatically send emails: staff review and select
teams in Email Management.

Team code sends use the same six-hour cooldown per team and email address as
judge code sends. Addresses are compared without regard to case, and each address
can receive at most one team email in a batch. Automatic and manual team sends
share the cooldown. Failed attempts are recorded but do not start a cooldown;
partial success starts a cooldown for that team and successful addresses. The
server rechecks eligibility when staff submit a selection.

## Deployment

Apply the migrations (`uv run python manage.py migrate`) and rebuild frontend
assets as usual. The new migration removes the unused legacy delivery flag and
roster baseline. Registrations, team codes, and email logs remain intact.

Teams without a recorded successful send show **Never sent** and are selected by
default when eligible. **Needs resend** compares the current team information and
recipients with recorded successful sends.

Older judge, registration-confirmation, and RFD logs remain visible. Their original
content was not saved, so history explicitly reports that the original content is
unavailable. Historical team emails and staff invitations cannot be reconstructed.
New sends save both plain text and HTML, and previews display the sandboxed HTML
version only.
