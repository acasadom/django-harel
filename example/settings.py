from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "example-only-do-not-use-in-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "urls"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_harel",
    "articles",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
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
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "example.db",
        # the worker and the server share the file: wait for its write lock instead of failing
        "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "static/"

USE_TZ = True

DJANGO_HAREL = {
    "MACHINES": {
        "publishing": "articles/machines/publishing.stm",
    },
    "TRACE": True,
}
