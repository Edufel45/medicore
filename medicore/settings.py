"""
MediCore — Django Settings
============================================================
Reads configuration from .env (via django-environ).
Supports SQLite (dev) and Supabase Postgres (prod) via DATABASE_URL.

Proposal reference: §2.3 Technology Stack
"""

from pathlib import Path
from datetime import timedelta
import environ

# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------
# Environment (.env)
# ------------------------------------------------------------
env = environ.Env(
    DJANGO_DEBUG=(bool, True),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DJANGO_TIME_ZONE=(str, "Africa/Nairobi"),
    DJANGO_LANGUAGE_CODE=(str, "en-us"),
    SHA_PROVIDER=(str, "mock"),
    DARAJA_ENV=(str, "sandbox"),
    SUPABASE_STORAGE_BUCKET=(str, "medicore"),
)

# Read .env if it exists
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    env.read_env(str(ENV_FILE))

# ------------------------------------------------------------
# Core
# ------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-key-change-me")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# ------------------------------------------------------------
# Applications
# ------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "crispy_forms",
    "crispy_tailwind",
    "widget_tweaks",
]

# Local apps (all live under apps/)
LOCAL_APPS = [
    "apps.accounts",
    "apps.patients",
    "apps.clinical",
    "apps.laboratory",
    "apps.pharmacy",
    "apps.billing",
    "apps.sha",
    "apps.mpesa",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ------------------------------------------------------------
# Middleware
# ------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",   # static files in prod
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Custom — audit logging
    "apps.accounts.middleware.AuditMiddleware",
]

ROOT_URLCONF = "medicore.urls"

# ------------------------------------------------------------
# Templates
# ------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Custom — inject role, theme, nav items into every template
                "apps.accounts.context_processors.role_context",
            ],
        },
    },
]

WSGI_APPLICATION = "medicore.wsgi.application"
ASGI_APPLICATION = "medicore.asgi.application"

# ------------------------------------------------------------
# Database
# SQLite in dev, Supabase Postgres in prod (via DATABASE_URL)
# ------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}
DATABASES["default"]["CONN_MAX_AGE"] = 60

# ------------------------------------------------------------
# Password validation
# ------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ------------------------------------------------------------
# I18N
# ------------------------------------------------------------
LANGUAGE_CODE = env("DJANGO_LANGUAGE_CODE")
TIME_ZONE = env("DJANGO_TIME_ZONE")
USE_I18N = True
USE_TZ = True

# ------------------------------------------------------------
# Static & media
# ------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Static files storage — manifests only in production
if DEBUG:
    STATICFILES_STORAGE_BACKEND = "django.contrib.staticfiles.storage.StaticFilesStorage"
else:
    STATICFILES_STORAGE_BACKEND = "whitenoise.storage.CompressedManifestStaticFilesStorage"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": STATICFILES_STORAGE_BACKEND,
    },
}
# ------------------------------------------------------------
# Default primary key
# ------------------------------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ------------------------------------------------------------
# Auth
# ------------------------------------------------------------
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# ------------------------------------------------------------
# DRF
# ------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

# ------------------------------------------------------------
# Crispy forms (Tailwind)
# ------------------------------------------------------------
CRISPY_ALLOWED_TEMPLATE_PACKS = "tailwind"
CRISPY_TEMPLATE_PACK = "tailwind"

# ------------------------------------------------------------
# Messages -> CSS classes (matches our Tailwind-style badges)
# ------------------------------------------------------------
from django.contrib.messages import constants as messages_constants  # noqa: E402
MESSAGE_TAGS = {
    messages_constants.DEBUG: "debug",
    messages_constants.INFO: "info",
    messages_constants.SUCCESS: "success",
    messages_constants.WARNING: "warning",
    messages_constants.ERROR: "error",
}

# ------------------------------------------------------------
# Email (console in dev)
# ------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@medicore.local")

# ------------------------------------------------------------
# ──────────────────────────────────────────────────────────────
# MediCore — custom settings
# ──────────────────────────────────────────────────────────────
# ------------------------------------------------------------

# ---- Supabase ----
SUPABASE_URL = env("SUPABASE_URL", default="")
SUPABASE_ANON_KEY = env("SUPABASE_ANON_KEY", default="")
SUPABASE_SERVICE_KEY = env("SUPABASE_SERVICE_KEY", default="")
SUPABASE_STORAGE_BUCKET = env("SUPABASE_STORAGE_BUCKET")

# ---- Safaricom Daraja (M-Pesa) ----
DARAJA_ENV = env("DARAJA_ENV")
DARAJA_CONSUMER_KEY = env("DARAJA_CONSUMER_KEY", default="")
DARAJA_CONSUMER_SECRET = env("DARAJA_CONSUMER_SECRET", default="")
DARAJA_SHORTCODE = env("DARAJA_SHORTCODE", default="174379")
DARAJA_PASSKEY = env("DARAJA_PASSKEY", default="")
DARAJA_CALLBACK_URL = env(
    "DARAJA_CALLBACK_URL",
    default="http://localhost:8000/mpesa/callback/stk/",
)
DARAJA_INITIATOR_NAME = env("DARAJA_INITIATOR_NAME", default="testapi")
DARAJA_INITIATOR_PASSWORD = env("DARAJA_INITIATOR_PASSWORD", default="")
DARAJA_SECURITY_CREDENTIAL = env("DARAJA_SECURITY_CREDENTIAL", default="")

# Base URL switches with environment
if DARAJA_ENV == "production":
    DARAJA_BASE_URL = "https://api.safaricom.co.ke"
else:
    DARAJA_BASE_URL = "https://sandbox.safaricom.co.ke"

# ---- SHA (Social Health Authority) ----
SHA_PROVIDER = env("SHA_PROVIDER")   # "mock" or "real"
SHA_API_BASE = env("SHA_API_BASE", default="")
SHA_API_KEY = env("SHA_API_KEY", default="")
SHA_FACILITY_CODE = env("SHA_FACILITY_CODE", default="")

# ------------------------------------------------------------
# Celery — async tasks, callbacks, reconciliation
# ------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60       # 30 min hard limit
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 min soft limit

# Serialization — JSON only
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# In DEBUG, run tasks synchronously (no Redis/worker needed for dev)
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=DEBUG)

# Beat: periodic tasks
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    # Reconcile stale M-Pesa transactions every 60 seconds
    "reconcile-mpesa-every-minute": {
        "task": "mpesa.reconcile_pending_txns",
        "schedule": 60.0,
    },
}

# ---- MediCore business constants ----
REGISTRATION_FEE = 200        # Ksh — collected at reception
CONSULTATION_FEE = 500        # Ksh — consultation charge
LOW_STOCK_THRESHOLD = 25      # Units — pharmacy low-stock alert
SHA_DEFAULT_COVERAGE_PCT = 70 # % SHA covers by default in demo

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
        "simple": {
            "format": "[{levelname}] {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "medicore": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}