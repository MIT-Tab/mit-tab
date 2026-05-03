import pytest
from django.core.cache import caches
from django.urls import reverse

from mittab.apps.registration.models import (
    InfoLink,
    RegistrationLink,
    validate_safe_url,
)
from django.core.exceptions import ValidationError


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
    from mittab.apps.tab.models import School
    from mittab.apps.registration.emails import (
        build_registration_confirmation_email,
    )
    from mittab.apps.registration.models import Registration

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
