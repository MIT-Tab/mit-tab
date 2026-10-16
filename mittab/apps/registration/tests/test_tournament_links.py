import pytest
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.urls import reverse

from mittab.apps.registration.emails import build_registration_confirmation_email
from mittab.apps.registration.models import (
    InfoLink,
    InfoText,
    Registration,
    RegistrationLink,
    validate_safe_url,
)
from mittab.apps.tab.models import School


@pytest.fixture(autouse=True)
def _clear_public_cache():
    caches["public"].clear()
    yield
    caches["public"].clear()


@pytest.fixture(name="logged_in_admin_client")
def fixture_logged_in_admin_client(client, django_user_model):
    user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password",
    )
    client.force_login(user)
    return client


def test_validate_safe_url_blocks_dangerous_schemes():
    validate_safe_url("https://example.com")
    validate_safe_url("http://example.com/path?x=1")
    for bad in (
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "data:text/html,<script>",
        "file:///etc/passwd",
        "vbscript:msgbox",
    ):
        with pytest.raises(ValidationError):
            validate_safe_url(bad)


@pytest.mark.django_db
def test_registration_label_identifies_school_and_email():
    school = School.objects.create(name="MIT")
    registration = Registration.objects.create(
        school=school,
        email="tab@mit.edu",
    )

    assert str(registration) == "MIT — tab@mit.edu"


@pytest.mark.django_db
def test_admin_can_create_info_link(logged_in_admin_client):
    response = logged_in_admin_client.post(
        reverse("tournament_link_create", kwargs={"link_kind": "info"}),
        {
            "title": "Tournament Discord",
            "url": "https://discord.gg/abc",
            "description": "Chat with staff",
            "display_order": 0,
            "is_active": "on",
        },
        follow=True,
    )
    assert response.status_code == 200
    link = InfoLink.objects.get()
    assert link.title == "Tournament Discord"
    assert link.url == "https://discord.gg/abc"
    assert link.is_active is True


@pytest.mark.django_db
def test_admin_can_create_update_and_delete_info_text(logged_in_admin_client):
    response = logged_in_admin_client.post(
        reverse("info_text_create"),
        {
            "title": "Day 1 GA",
            "text": "Student center, room 201",
            "display_order": 2,
            "is_active": "on",
        },
        follow=True,
    )
    assert response.status_code == 200
    info_text = InfoText.objects.get(title="Day 1 GA")
    assert info_text.text == "Student center, room 201"
    assert info_text.is_active is True

    response = logged_in_admin_client.post(
        reverse("info_text_update", kwargs={"info_text_id": info_text.pk}),
        {
            "title": "Day 2 GA",
            "text": "Building 26 lobby",
            "display_order": 3,
        },
        follow=True,
    )
    assert response.status_code == 200
    info_text.refresh_from_db()
    assert info_text.title == "Day 2 GA"
    assert info_text.text == "Building 26 lobby"
    assert info_text.display_order == 3
    assert info_text.is_active is False

    response = logged_in_admin_client.post(
        reverse("info_text_delete", kwargs={"info_text_id": info_text.pk}),
        follow=True,
    )
    assert response.status_code == 200
    assert not InfoText.objects.filter(pk=info_text.pk).exists()


@pytest.mark.django_db
def test_public_homepage_editor_renders_info_text_controls(logged_in_admin_client):
    info_text = InfoText.objects.create(title="Day 1 GA", text="Lobby")

    response = logged_in_admin_client.get(reverse("public_homepage"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Text information" in body
    assert reverse("info_text_update", kwargs={"info_text_id": info_text.pk}) in body
    assert 'name="text"' in body
    assert "Day 1 GA" in body
    assert "Lobby" in body


@pytest.mark.django_db
def test_admin_rejects_javascript_url(logged_in_admin_client):
    response = logged_in_admin_client.post(
        reverse("tournament_link_create", kwargs={"link_kind": "info"}),
        {
            "title": "Bad",
            "url": "javascript:alert(1)",
            "description": "",
            "display_order": 0,
            "is_active": "on",
        },
        follow=True,
    )
    assert response.status_code == 200
    assert InfoLink.objects.count() == 0


@pytest.mark.django_db
def test_admin_can_update_registration_link(logged_in_admin_client):
    link = RegistrationLink.objects.create(
        title="Old", url="https://example.com/a"
    )
    response = logged_in_admin_client.post(
        reverse(
            "tournament_link_update",
            kwargs={"link_kind": "registration", "link_id": link.pk},
        ),
        {
            "title": "New",
            "url": "https://example.com/b",
            "description": "Updated",
            "display_order": 5,
            "is_active": "on",
        },
        follow=True,
    )
    assert response.status_code == 200
    link.refresh_from_db()
    assert link.title == "New"
    assert link.url == "https://example.com/b"
    assert link.description == "Updated"
    assert link.display_order == 5


@pytest.mark.django_db
def test_admin_can_delete_link(logged_in_admin_client):
    link = InfoLink.objects.create(title="Bye", url="https://example.com")
    response = logged_in_admin_client.post(
        reverse(
            "tournament_link_delete",
            kwargs={"link_kind": "info", "link_id": link.pk},
        ),
        follow=True,
    )
    assert response.status_code == 200
    assert InfoLink.objects.count() == 0


@pytest.mark.django_db
def test_unknown_link_kind_404(logged_in_admin_client):
    response = logged_in_admin_client.post(
        "/registration/manage/links/totally-fake/",
        {"title": "x", "url": "https://example.com"},
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_public_home_renders_info_links(client):
    InfoLink.objects.create(
        title="Packet",
        url="https://drive.google.com/abc",
        description="Tournament packet",
    )
    InfoLink.objects.create(
        title="Hidden", url="https://example.com/x", is_active=False
    )
    response = client.get(reverse("public_home"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Packet" in body
    assert "https://drive.google.com/abc" in body
    assert "Hidden" not in body


@pytest.mark.django_db
def test_public_home_renders_active_info_text_as_non_link(client):
    InfoText.objects.create(title="GA", text="Lobby at 8:30 AM")
    InfoText.objects.create(title="Hidden", text="Secret", is_active=False)

    response = client.get(reverse("public_home"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "External Information" in body
    assert '<div class="info-text-chip">' in body
    assert "GA:" in body
    assert "Lobby at 8:30 AM" in body
    assert "Secret" not in body
    assert '<a class="info-text-chip"' not in body


@pytest.mark.django_db
def test_public_home_escapes_info_text(client):
    InfoText.objects.create(title="GA", text="<script>alert(1)</script>")

    response = client.get(reverse("public_home"))
    body = response.content.decode()

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


@pytest.mark.django_db
def test_info_text_defaults_are_idempotent():
    InfoText.objects.all().delete()

    first, first_created = InfoText.ensure_defaults()
    second, second_created = InfoText.ensure_defaults()

    assert first_created is True
    assert second_created is False
    assert first.pk == second.pk
    assert first.title == "GA"
    assert first.text == "TBD"


@pytest.mark.django_db
def test_public_home_escapes_link_title(client):
    InfoLink.objects.create(
        title="<script>alert(1)</script>",
        url="https://example.com",
    )
    response = client.get(reverse("public_home"))
    body = response.content.decode()
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


@pytest.mark.django_db
def test_confirmation_email_includes_followup_links(rf):
    school = School.objects.create(name="Email U")
    registration = Registration.objects.create(
        school=school, email="contact@example.com"
    )
    RegistrationLink.objects.create(
        title="Hotel Form",
        url="https://example.com/hotel",
        description="Reserve rooms",
    )
    RegistrationLink.objects.create(
        title="Inactive", url="https://example.com/no", is_active=False
    )

    request = rf.get("/registration/")
    email_request = build_registration_confirmation_email(registration, request)

    assert "Hotel Form" in email_request.text_body
    assert "https://example.com/hotel" in email_request.text_body
    assert "Inactive" not in email_request.text_body
    assert "Hotel Form" in email_request.html_body
    assert "https://example.com/hotel" in email_request.html_body
