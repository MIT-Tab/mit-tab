from django.urls import path

from . import views

urlpatterns = [
    path("", views.registration_portal, name="registration_portal"),
    path("manage/", views.registration_admin, name="registration_admin"),
    path(
        "manage/links/<str:link_kind>/",
        views.tournament_link_create,
        name="tournament_link_create",
    ),
    path(
        "manage/links/<str:link_kind>/<int:link_id>/",
        views.tournament_link_update,
        name="tournament_link_update",
    ),
    path(
        "manage/links/<str:link_kind>/<int:link_id>/delete/",
        views.tournament_link_delete,
        name="tournament_link_delete",
    ),
    path("edit/", views.registration_code_lookup, name="registration_code_lookup"),
    path("<str:code>/", views.registration_portal, name="registration_portal_edit"),
    path("api/debaters/<int:school_id>/", views.proxy_debaters, name="api_debaters"),
    path(
        "api/debater-email-status/",
        views.proxy_debater_email_status,
        name="api_debater_email_status",
    ),
]
