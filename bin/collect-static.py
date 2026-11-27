#!/usr/bin/env python3
"""Collect webpack output and the locked Django admin assets for Rust."""

import sys
from pathlib import Path

import django
from django.conf import settings
from django.core.management import call_command


def main():
    """Use the same finder order as the Django application."""
    assets, destination = (str(Path(path).resolve()) for path in sys.argv[1:])
    settings.configure(
        INSTALLED_APPS=(
            "django.contrib.admin.apps.SimpleAdminConfig",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.staticfiles",
        ),
        STATIC_URL="/static/",
        STATIC_ROOT=destination,
        STATICFILES_DIRS=(assets,),
    )
    django.setup()
    call_command("collectstatic", interactive=False)


if __name__ == "__main__":
    main()
