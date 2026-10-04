"""The bench's settings: the test app, on a real database (not a test one) picked by
`HAREL_TEST_DB` like the tests. SQLite uses `bench/bench.db`."""

from pathlib import Path

from tests.settings import *  # noqa: F403
from tests.settings import _DB, DATABASES

BASE_DIR = Path(__file__).resolve().parent.parent

if _DB == "sqlite":
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "bench" / "bench.db",
        "OPTIONS": {"timeout": 60, "transaction_mode": "IMMEDIATE"},
    }

DJANGO_HAREL = {"MACHINES": {"pipeline": "bench/pipeline.stm"}}

LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
