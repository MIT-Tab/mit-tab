from django.urls import path

from . import views

urlpatterns = [
    path("", views.registration_portal, name="registration_portal"),
    path("manage/", views.registration_admin, name="registration_admin"),
    path("edit/", views.registration_code_lookup, name="registration_code_lookup"),
    path("<str:code>/", views.registration_portal, name="registration_portal_edit"),
    path("api/debaters/<int:school_id>/", views.proxy_debaters, name="api_debaters"),
]
