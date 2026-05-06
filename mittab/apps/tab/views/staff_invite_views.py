from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.views import PasswordResetCompleteView, PasswordResetConfirmView
from django.shortcuts import redirect, render, reverse

from mittab.apps.tab.auth_roles import (
    PRESET_FULL_TAB_STAFF,
    STAFF_PRESET_CHOICES,
    STAFF_PRESET_DETAILS,
)
from mittab.apps.tab.helpers import redirect_and_flash_error, redirect_and_flash_success
from mittab.apps.tab.staff_invites import (
    get_or_create_staff_invite_user,
    send_staff_invite_email,
)
from mittab.libs.email_service import EmailServiceError


def is_superuser(user):
    return user.is_authenticated and user.is_superuser


class StaffInviteForm(forms.Form):
    email = forms.EmailField(label="Email")
    username = forms.CharField(
        label="Username",
        max_length=150,
        help_text=(
            "This is the username the invitee will use to sign in. For an "
            "existing staff account, enter that account's current username."
        ),
    )
    permission_preset = forms.ChoiceField(
        label="Permission preset",
        choices=STAFF_PRESET_CHOICES,
        initial=PRESET_FULL_TAB_STAFF,
        required=False,
        help_text=(
            "Choose the narrowest role this staffer needs. Only full power "
            "tab staff can access the entire admin surface."
        ),
    )

    def clean_email(self):
        User = get_user_model()
        email = User.objects.normalize_email(self.cleaned_data["email"]).strip()
        user = User.objects.filter(email__iexact=email).first()
        if user and not user.is_staff:
            raise forms.ValidationError(
                "A non-staff account already uses this email. Update it in "
                "the admin interface before sending an invite."
            )
        return email

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        username = (cleaned_data.get("username") or "").strip()
        if not email or not username:
            return cleaned_data

        User = get_user_model()
        user_with_email = User.objects.filter(email__iexact=email).first()
        user_with_username = User.objects.filter(username=username).first()

        if user_with_email and user_with_email.username != username:
            self.add_error(
                "username",
                (
                    "That email already belongs to the staff username "
                    f"'{user_with_email.username}'."
                ),
            )
        elif user_with_username and user_with_username.email.lower() != email.lower():
            self.add_error(
                "username",
                "A different account already uses this username.",
            )

        return cleaned_data

    def clean_username(self):
        User = get_user_model()
        username = self.cleaned_data["username"].strip()
        validator = User._meta.get_field(User.USERNAME_FIELD).validators[0]
        try:
            validator(username)
        except forms.ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return username

    def clean_permission_preset(self):
        return self.cleaned_data["permission_preset"] or PRESET_FULL_TAB_STAFF


@user_passes_test(is_superuser, login_url="/403/")
def invite_staff(request):
    if request.method == "POST":
        form = StaffInviteForm(request.POST)
        if form.is_valid():
            user, _created = get_or_create_staff_invite_user(
                form.cleaned_data["email"],
                form.cleaned_data["username"],
                form.cleaned_data["permission_preset"],
            )
            if not user.is_staff:
                return redirect_and_flash_error(
                    request,
                    "A non-staff account already uses this email. Update it in "
                    "the admin interface before sending an invite.",
                    path=reverse("invite_staff"),
                )
            try:
                send_staff_invite_email(request, user)
            except EmailServiceError as exc:
                return redirect_and_flash_error(
                    request,
                    f"Could not send staff invite: {exc}",
                    path=reverse("invite_staff"),
                )
            return redirect_and_flash_success(
                request,
                f"Sent staff invite to {form.cleaned_data['email']}.",
                path=reverse("invite_staff"),
            )
    else:
        form = StaffInviteForm()

    return render(
        request,
        "tab/invite_staff.html",
        {
            "title": "Invite Tab Staff",
            "form": form,
            "staff_preset_details": STAFF_PRESET_DETAILS,
        },
    )


class StaffInviteConfirmView(PasswordResetConfirmView):
    template_name = "registration/staff_invite_confirm.html"
    success_url = "/public/staff-invite/complete/"


class StaffInviteCompleteView(PasswordResetCompleteView):
    template_name = "registration/staff_invite_complete.html"
