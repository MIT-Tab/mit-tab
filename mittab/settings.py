# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
import os
BASE_DIR = os.path.dirname(os.path.dirname(__file__))

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/1.6/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "=#)rtpjhx_dl+p(1c8)1qu36%v2@wv@nhrg&6@kjw!ga2va!5$"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DEBUG")

ALLOWED_HOSTS = ["*"]

# Application definition

INSTALLED_APPS = ("django.contrib.admin", "django.contrib.auth",
                  "django.contrib.contenttypes", "django.contrib.sessions",
                  "django.contrib.messages", "django.contrib.staticfiles",
                  "mittab.apps.tab", "raven.contrib.django.raven_compat",
                  "webpack_loader", "bootstrap4",)

MIDDLEWARE = (
    "mittab.apps.tab.middleware.FailoverDuringBackup",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "mittab.apps.tab.middleware.Login",
)

if os.environ.get("SILK_ENABLED"):
    INSTALLED_APPS = INSTALLED_APPS + ("silk",)
    MIDDLEWARE = MIDDLEWARE + ("silk.middleware.SilkyMiddleware",)
    SILK_ENABLED = True
else:
    SILK_ENABLED = False

ROOT_URLCONF = "mittab.urls"

WSGI_APPLICATION = "mittab.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "mittab",
        "OPTIONS": {"charset": "utf8mb4"},
        "USER": os.environ.get("MYSQL_USER", "root"),
        "PASSWORD": os.environ.get("MYSQL_ROOT_PASSWORD", ""),
        "HOST": os.environ.get("MITTAB_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("MYSQL_PORT", "3306"),
    }
}

# Error monitoring
# https://docs.sentry.io/clients/python/integrations/django/
if os.environ.get("SENTRY_DSN"):
    RAVEN_CONFIG = {"dsn": os.environ["SENTRY_DSN"]}

# Internationalization
# https://docs.djangoproject.com/en/1.6/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_L10N = True

USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

STATICFILES_DIRS = (os.path.join(BASE_DIR, "assets"), )

WEBPACK_LOADER = {
    "DEFAULT": {
        "BUNDLE_DIR_NAME": "webpack_bundles/",
        "STATS_FILE": os.path.join(BASE_DIR, "webpack-stats.json"),
    }
}

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            os.path.join(BASE_DIR, "mittab", "templates"),
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "debug":
            True,
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.debug",
                "django.template.context_processors.i18n",
                "django.template.context_processors.media",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.request",
            ],
        },
    },
]

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

SETTING_YAML_PATH = os.path.join(BASE_DIR, "settings.yaml")

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    },
    'filesystem': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': '/var/tmp/django_cache',
    }
}

"""
LOGGING = {
    'version': 1,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django.db.backends': {
            'level': 'DEBUG',
        },
    },
    'root': {
        'handlers': ['console'],
    }
}
"""
