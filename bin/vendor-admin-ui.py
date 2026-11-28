#!/usr/bin/env python3
"""Copy the locked Django admin templates and export its superuser menu to Rust."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import django

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "rust/server/templates/django_admin"
TEMPLATES = (
    "app_index.html", "app_list.html", "base.html", "change_form.html",
    "change_form_object_tools.html", "color_theme_toggle.html", "includes/fieldset.html",
    "nav_sidebar.html", "prepopulated_fields_js.html", "submit_line.html",
    "widgets/related_widget_wrapper.html", "change_list.html", "change_list_results.html",
    "change_list_object_tools.html", "actions.html", "pagination.html", "object_history.html",
    "delete_confirmation.html", "delete_selected_confirmation.html", "includes/object_delete_summary.html",
)


def main():
    """Read sources and URL metadata without connecting to a tournament database."""
    sys.path.insert(0, str(PROJECT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mittab.settings")
    django.setup()
    from django.contrib import admin
    from django.contrib.auth import get_user_model
    from django.core.serializers.json import DjangoJSONEncoder
    from django.template.loader import get_template
    from django.test import RequestFactory
    from mittab.apps.registration.admin import RegistrationConfigAdmin
    from mittab.apps.tab.admin import ExperimentalAdmin

    for name in TEMPLATES:
        source = Path(get_template(f"admin/{name}").origin.name)
        destination = ROOT / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    request = RequestFactory().get("/admin/")
    request.user = get_user_model()(is_superuser=True, is_staff=True, is_active=True)
    with patch.object(ExperimentalAdmin, "enabled", return_value=True), patch.object(
        RegistrationConfigAdmin, "has_add_permission", return_value=True,
    ):
        apps = admin.site.get_app_list(request)
    for app in apps:
        for model in app["models"]:
            model.pop("model")
            model.pop("perms")
    (ROOT / "applications.json").write_text(
        json.dumps(apps, indent=2, ensure_ascii=False, cls=DjangoJSONEncoder) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
