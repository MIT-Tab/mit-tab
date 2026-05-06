from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from mittab.apps.tab.auth_roles import apply_staff_permission_preset
from mittab.libs.email_service import EmailService
from mittab.libs.email_views import build_staff_invite_email


def build_staff_invite_url(request, user):
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("staff_invite_confirm", args=[uidb64, token])
    return request.build_absolute_uri(path)


def send_staff_invite_email(request, user):
    invite_url = build_staff_invite_url(request, user)
    email_request = build_staff_invite_email(
        user.email,
        user.username,
        invite_url,
    )
    return EmailService().send_bulk([email_request])


def get_or_create_staff_invite_user(email, username, permission_preset):
    user_model = get_user_model()
    normalized_email = user_model.objects.normalize_email(email).strip()
    existing_user = user_model.objects.filter(email__iexact=normalized_email).first()

    if existing_user:
        apply_staff_permission_preset(existing_user, permission_preset)
        return existing_user, False

    user = user_model(
        username=username.strip(),
        email=normalized_email,
        is_active=True,
    )
    user.set_unusable_password()
    user.save()
    apply_staff_permission_preset(user, permission_preset)
    return user, True
