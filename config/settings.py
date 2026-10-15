"""Settings for Deploy Шалгагч. All secrets come from environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-secret")
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "checker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
            "checker.context.workspace",
        ]},
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite keeps the MVP simple. On Render's free plan the disk is ephemeral,
# so saved services are lost on redeploy; point DATABASE_PATH at a disk to keep them.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DATABASE_PATH", BASE_DIR / "db.sqlite3"),
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30

# Hosting is simulated until the AWS setup in HOSTING_PLAN.md exists; shows a "Демо" badge.
DEMO_MODE = os.environ.get("DEMO_MODE", "1") != "0"
# Elastic IP of the gateway; customers point apex domains here. 203.0.113.0/24 is a documentation range.
GATEWAY_IP = os.environ.get("GATEWAY_IP", "203.0.113.10")

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"

LANGUAGE_CODE = "mn"
TIME_ZONE = "Asia/Ulaanbaatar"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# collectstatic target; nginx serves it directly in production (deploy/nginx-aidolab.conf).
STATIC_ROOT = BASE_DIR / "staticfiles"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Start HSTS at one day; raise to a year once HTTPS has run cleanly for a while.
    SECURE_HSTS_SECONDS = 60 * 60 * 24
    # Student apps live on sibling subdomains (*.aidolab.mn); the __Host- prefix
    # stops them from overwriting the dashboard's cookies (cookie tossing).
    SESSION_COOKIE_NAME = "__Host-sessionid"
    CSRF_COOKIE_NAME = "__Host-csrftoken"
