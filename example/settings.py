SECRET_KEY = "example-only-do-not-use-in-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "urls"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django_harel",
    "articles",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "example.db",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True

DJANGO_HAREL = {
    "MACHINES": {
        "publishing": "articles/machines/publishing.stm",
    },
    "CONCURRENCY": 4,
    "VISIBILITY": 30.0,
}
