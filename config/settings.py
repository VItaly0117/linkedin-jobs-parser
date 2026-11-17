from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-key")
DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "jobs",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {
    "default": env.db(
        "DATABASE_URL", default="postgres://linkedin:linkedin@localhost:5432/linkedin"
    )
}

LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Парсер LinkedIn ---
LINKEDIN_RPS = env.float("LINKEDIN_RPS", default=1.0)
LINKEDIN_MAX_RETRIES = env.int("LINKEDIN_MAX_RETRIES", default=6)
LINKEDIN_TIMEOUT = env.int("LINKEDIN_TIMEOUT", default=30)
LINKEDIN_MAX_ATTEMPTS = env.int("LINKEDIN_MAX_ATTEMPTS", default=5)
LINKEDIN_PROXIES = [p for p in env.list("LINKEDIN_PROXIES", default=[]) if p]
# Вакансии/компании, «зависшие» в in_progress дольше этого времени, возвращаются в очередь.
LINKEDIN_STALE_LOCK_MINUTES = env.int("LINKEDIN_STALE_LOCK_MINUTES", default=15)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {"jobs": {"handlers": ["console"], "level": "INFO"}},
}
