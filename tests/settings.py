"""Test settings. `HAREL_TEST_DB` picks the database: `sqlite` (default), `postgres` or
`mysql`; the server ones read `HAREL_TEST_POSTGRES` / `HAREL_TEST_MYSQL` as
`host:port:user:password:name`."""

import os

_DB = os.environ.get("HAREL_TEST_DB", "sqlite")


def _server(var: str, default: str) -> dict:
    host, port, user, password, name = os.environ.get(var, default).split(":")
    return {"HOST": host, "PORT": port, "USER": user, "PASSWORD": password, "NAME": name}


if _DB == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            **_server("HAREL_TEST_POSTGRES", "127.0.0.1:55432:postgres:harel:harel"),
        }
    }
elif _DB == "mysql":
    import pymysql

    # PyMySQL stands in for mysqlclient (no MySQL client library needed to run the tests)
    pymysql.version_info = (2, 2, 7, "final", 0)
    pymysql.install_as_MySQLdb()
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            **_server("HAREL_TEST_MYSQL", "127.0.0.1:53306:root:harel:harel"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django_harel",
    "tests.app",
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SECRET_KEY = "tests-only"

USE_TZ = True

ROOT_URLCONF = "tests.urls"

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
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

DJANGO_HAREL = {
    "MACHINES": {
        "job": "tests/machines/job.stm",
        "order": "tests/machines/order.stm",
        "fulfilment": "tests/machines/fulfilment.stm",
    },
}
