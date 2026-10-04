"""Shared fixtures for django-harel tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _empty_harel_tables(request):
    """Django's flush between `transaction=True` tests skips harel's tables (no managed model
    owns them): empty them after each test that touched the database."""
    yield
    if "django_db_setup" not in request.fixturenames:
        return
    from django.db import connection

    from django_harel.schema import TABLES

    with connection.cursor() as cur:
        for table in TABLES:
            cur.execute(f"DELETE FROM {table}")
