from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from mittab.libs.email_service import EmailRequest, EmailService


def build_staff_invite_url(request, user):
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("staff_invite_confirm", args=[uidb64, token])
    return request.build_absolute_uri(path)


def send_staff_invite_email(request, user):
    invite_url = build_staff_invite_url(request, user)
    subject = "You're invited to join MIT-TAB staff"
    text_body = (
        "You've been invited to join the MIT-TAB staff site.\n\n"
        f"Username: {user.username}\n\n"
        "Use this link to set your password and finish creating your account:\n"
        f"{invite_url}\n\n"
        "If you were not expecting this invitation, you can ignore this email."
    )
    email_request = EmailRequest(
        to_address=user.email,
        subject=subject,
        text_body=text_body,
    )
    return EmailService().send_bulk([email_request])


def get_or_create_staff_invite_user(email, username):
    User = get_user_model()
    normalized_email = User.objects.normalize_email(email).strip()
    existing_user = User.objects.filter(email__iexact=normalized_email).first()

    if existing_user:
        return existing_user, False

    user = User(
        username=username.strip(),
        email=normalized_email,
        is_staff=True,
        is_superuser=True,
        is_active=True,
    )
    user.set_unusable_password()
    user.save()
    return user, True
