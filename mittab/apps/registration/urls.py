from django.urls import path

from . import views

urlpatterns = [
    path("", views.registration_portal, name="registration_portal"),
    path("setup/", views.registration_setup, name="registration_setup"),
    path("<str:code>/", views.registration_portal, name="registration_portal_edit"),
    # API proxy endpoints to avoid CORS issues
    path("api/schools/", views.proxy_schools_active, name="api_schools_active"),
    path("api/schools/all/", views.proxy_schools_all, name="api_schools_all"),
    path("api/debaters/<int:school_id>/", views.proxy_debaters, name="api_debaters"),
]
