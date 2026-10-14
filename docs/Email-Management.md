# Email Management

Full tab staff can use **Admin > Email Management** to preview and send team
codes, judge codes, registration confirmations, and written RFDs. Choose an email
type, review its current preview, select recipients, and send. **Sent Email
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

## Updating a running tournament

Back up the tournament database, apply the normal deployment with migrations
(`uv run python manage.py migrate`), and rebuild the frontend assets as usual.
Both the `tab` and `registration` apps have additive migrations. These migrations
preserve registrations, team codes, relationships, and existing email logs; they
do not send emails. Deploy the migrations before directing requests to the new
application code, and avoid roster writes while the baseline is being recorded.

Older team emails had no delivery log. Existing teams therefore start with
**Earlier delivery unknown**, and are excluded from the default send selection
and **Select Never Sent**. Directors can deliberately select them to send their
current codes. The migration records a baseline of the current roster, so later
edits can still trigger **Needs resend** without pretending the earlier email was
successfully delivered. After a logged successful send, normal snapshot tracking
applies.

Older judge, registration-confirmation, and RFD logs remain visible. Their original
content was not saved, so history explicitly reports that the original content is
unavailable. Historical team emails and staff invitations cannot be reconstructed.
New sends save both plain text and HTML for previews. HTML previews are sandboxed.
