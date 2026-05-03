from decimal import Decimal

import pytest
from django.urls import reverse

from mittab.apps.registration.models import (
    Registration,
    RegistrationChangeLog,
    RegistrationConfig,
)
from mittab.apps.tab.models import Judge, School, Team


@pytest.fixture(name="logged_in_admin_client")
def fixture_logged_in_admin_client(client, django_user_model):
    user = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password",
    )
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_admin_list_shows_registration(logged_in_admin_client):
    school = School.objects.create(name="Admin U")
    registration = Registration.objects.create(
        school=school,
        email="contact@example.com",
    )
    team = Team.objects.create(
        name="Admin Team",
        school=school,
        registration=registration,
        seed=Team.UNSEEDED,
    )
    judge = Judge.objects.create(
        name="Admin Judge",
        rank=Decimal("6"),
        email="judge@example.com",
        registration=registration,
    )
    judge.schools.add(school)
    url = reverse("registration_admin")

    response = logged_in_admin_client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    assert "Admin U" in content
    assert team.name in content
    assert judge.name in content


@pytest.mark.django_db
def test_admin_can_delete_registration(logged_in_admin_client):
    school = School.objects.create(name="Delete U")
    registration = Registration.objects.create(
        school=school,
        email="delete@example.com",
    )
    Team.objects.create(
        name="Delete Team",
        school=school,
        registration=registration,
        seed=Team.UNSEEDED,
    )
    judge = Judge.objects.create(
        name="Delete Judge",
        rank=Decimal("5"),
        email="delete.judge@example.com",
        registration=registration,
    )
    judge.schools.add(school)
    url = reverse("registration_admin")

    response = logged_in_admin_client.post(
        url,
        {"registration_id": registration.id},
        follow=True,
    )

    assert response.status_code == 200
    assert Registration.objects.count() == 0
    assert Team.objects.count() == 0
    assert Judge.objects.count() == 0
    log = RegistrationChangeLog.objects.get()
    assert log.action == RegistrationChangeLog.DELETED
    assert log.registration is None
    assert log.registration_code == registration.herokunator_code


@pytest.mark.django_db
def test_admin_can_update_settings(logged_in_admin_client):
    config = RegistrationConfig.get_or_create_active()
    config.allow_new_registrations = False
    config.allow_registration_edits = False
    config.save()

    url = reverse("registration_admin")
    response = logged_in_admin_client.post(
        url,
        {
            "allow_new_registrations": "on",
            "allow_registration_edits": "on",
        },
        follow=True,
    )

    assert response.status_code == 200
    config.refresh_from_db()
    assert config.allow_new_registrations is True
    assert config.allow_registration_edits is True


@pytest.mark.django_db
def test_registration_config_is_singleton():
    config = RegistrationConfig.objects.create(
        allow_new_registrations=False,
        allow_registration_edits=False,
    )
    assert config.pk == RegistrationConfig.SINGLETON_PK

    active = RegistrationConfig.get_or_create_active()
    active.allow_new_registrations = True
    active.save()

    assert RegistrationConfig.objects.count() == 1
    config.refresh_from_db()
    assert config.allow_new_registrations is True
