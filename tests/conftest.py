"""Shared fixtures for django-harel tests."""

from __future__ import annotations

from pathlib import Path

import pytest

MACHINES_DIR = Path(__file__).parent / "machines"


@pytest.fixture
def job_definition():
    """A simple 3-state machine: Pending → Running → Done|Failed."""
    from harel.dsl import definition_from_dsl_file

    return definition_from_dsl_file(MACHINES_DIR / "job.stm")


@pytest.fixture
def store(db):
    """A DjangoStore backed by the test database."""
    from django_harel.store import DjangoStore

    return DjangoStore()


@pytest.fixture
def runner(job_definition, db):
    """A DjangoRunner wired to the job machine and test DB."""
    from django_harel.runner import DjangoRunner

    return DjangoRunner(definitions={"job": job_definition})
