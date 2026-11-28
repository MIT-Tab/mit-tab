import pytest
from django.urls import reverse

from mittab.apps.registration.models import RegistrationConfig
from mittab.apps.registration.tests.test_registration_admin import (  # pylint: disable=unused-import
    fixture_logged_in_admin_client,
)
from mittab.apps.tab.models import Judge, School, Scratch, TabSettings, Team
from mittab.libs.cacheing import cache_logic


@pytest.fixture(autouse=True)
def isolate_tab_settings_cache(settings, tmp_path):
    alias = cache_logic.PERSISTENT
    # Database rollbacks do not discard the cached values from earlier tests.
    settings.CACHES = {
        **settings.CACHES,
        alias: {
            **settings.CACHES[alias],
            "LOCATION": str(tmp_path / "tab-settings-cache"),
        },
    }


@pytest.mark.django_db
def test_opening_disc_scratches_requires_judge_list_publication_confirmation(
        logged_in_admin_client):
    TabSettings.set("judges_public", 0)
    # Warm the cache before changing visibility.
    assert TabSettings.get("judges_public") == 0
    url = reverse("registration_admin")
    settings_data = {"disc_scratches_open": "on", "disc_scratch_quantity": "2"}

    response = logged_in_admin_client.post(url, settings_data)
    assert response.status_code == 200
    assert "disc_scratches_open" in response.context["form"].errors
    assert not RegistrationConfig.get_active().disc_scratches_open
    assert TabSettings.get("judges_public") == 0

    response = logged_in_admin_client.post(
        url, {**settings_data, "confirm_judge_list_publication": "true"},
    )
    assert response.status_code == 302
    assert RegistrationConfig.get_active().disc_scratches_open
    assert TabSettings.get("judges_public") == 1
    public_response = logged_in_admin_client.get(reverse("public_judges"))
    assert public_response.status_code == 200


@pytest.mark.django_db
def test_judge_list_cannot_be_hidden_until_disc_scratches_close(logged_in_admin_client):
    TabSettings.set("judges_public", 1)
    registration_url = reverse("registration_admin")
    tab_settings_url = reverse("settings_form")
    response = logged_in_admin_client.post(registration_url, {
        "disc_scratches_open": "on",
        "disc_scratch_quantity": "1",
    })
    assert response.status_code == 302  # Public list needs no confirmation.

    response = logged_in_admin_client.get(tab_settings_url)
    assert response.context["form"].fields["setting_judges_public"].disabled
    assert b"Close disc scratches before hiding the judge list." in response.content

    # An unchecked checkbox is omitted from the POST, as it would be in a stale form.
    form_data = {
        field.name: field.value()
        for field in response.context["form"]
        if field.value() is not None and field.name != "setting_judges_public"
    }
    response = logged_in_admin_client.post(tab_settings_url, form_data)
    assert response.status_code == 302, response.context["form"].errors
    assert TabSettings.get("judges_public") == 1

    logged_in_admin_client.post(registration_url, {"disc_scratch_quantity": "1"})
    assert TabSettings.get("judges_public") == 1
    response = logged_in_admin_client.post(tab_settings_url, form_data)
    assert response.status_code == 302, response.context["form"].errors
    assert TabSettings.get("judges_public") == 0


@pytest.mark.django_db
def test_team_portal_with_hidden_judge_list_never_exposes_scratch_roster(client):
    # A legacy or imported configuration can have scratches on and the list hidden.
    RegistrationConfig.objects.create(disc_scratches_open=True, disc_scratch_quantity=1)
    TabSettings.set("judges_public", 0)
    school = School.objects.create(name="Portal U")
    team = Team.objects.create(name="Portal Team", school=school, seed=Team.UNSEEDED)
    existing_judge = Judge.objects.create(name="Previously scratched judge", rank=5)
    other_judge = Judge.objects.create(name="Unpublished judge", rank=5)
    scratch = Scratch.objects.create(
        team=team, judge=existing_judge, scratch_type=Scratch.TEAM_SCRATCH,
    )
    url = reverse("team_portal", args=[team.team_code])

    for response in (
        client.get(url),
        client.post(url, {
            "action": "update_scratches", "scratch_0": str(other_judge.pk),
        }),
    ):
        assert response.status_code == 200
        assert b"Disc scratches are currently closed." in response.content
        assert b"disc-scratch-modal" not in response.content
        assert existing_judge.name.encode() not in response.content
        assert other_judge.name.encode() not in response.content

    assert list(Scratch.objects.filter(team=team)) == [scratch]
