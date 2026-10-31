"""The tables django-harel's migrations create: harel's own SQL schema (see
`migrations/0001_initial.py`, which holds it frozen, per database vendor).

On SQLite and PostgreSQL the schema is harel's, statement for statement, so harel's native
backends can run over the same tables (`create_schema=False`). On MySQL/MariaDB the tables follow
the PostgreSQL layout, and only `DjangoStore` / `DjangoTransport` use them.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

TABLES = (
    "harel_executions",
    "harel_outbox",
    "harel_processed_events",
    "harel_timers",
    "harel_spawns",
    "harel_trace",
    "harel_definitions",
    "harel_workers",
    "harel_transport_messages",
    "harel_transport_groups",
)

# every table a test or a fixture should empty between runs
ALL_TABLES = TABLES


SUPPORTED_VENDORS = ("sqlite", "postgresql", "mysql")


def check_vendor(vendor: str) -> None:
    if vendor not in SUPPORTED_VENDORS:
        raise ImproperlyConfigured(
            f"django-harel supports the {', '.join(SUPPORTED_VENDORS)} database backends, not {vendor!r}"
        )
