"""The tables django-harel's migrations create: harel's own SQL schema, per database vendor.

harel versions its schema (`harel.engine.schema.SCHEMA_VERSION`); this is version
`_frozen_schema.SCHEMA_VERSION`. When harel's moves on, a new migration takes it there.

On SQLite and PostgreSQL the schema is harel's, statement for statement (see `_frozen_schema`),
so harel's native backends can run over the same tables (`create_schema=False`). harel has no
MySQL backend: on MySQL/MariaDB the tables follow the PostgreSQL layout — the transport keeps
its lease on the group row — and only `DjangoStore` / `DjangoTransport` use them.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from django_harel import _frozen_schema as frozen

PREFIX = "harel"

# MySQL: ids are case-sensitive (binary collation), and every indexed text column is a
# VARCHAR — MySQL can't index TEXT. 255 utf8mb4 characters keep each key within InnoDB's
# 3072-byte limit, the two-column ones included.
_MYSQL_TABLE = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"

MYSQL_STORE = (
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_executions (id VARCHAR(255) PRIMARY KEY, "
    f"definition_id VARCHAR(255) NOT NULL, data LONGTEXT NOT NULL, version INT NOT NULL) {_MYSQL_TABLE}",
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_outbox (seq BIGINT AUTO_INCREMENT PRIMARY KEY, "
    f"target_id VARCHAR(255), event LONGTEXT NOT NULL) {_MYSQL_TABLE}",
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_processed_events (execution_id VARCHAR(255) NOT NULL, "
    f"event_id VARCHAR(255) NOT NULL, PRIMARY KEY (execution_id, event_id)) {_MYSQL_TABLE}",
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_timers (execution_id VARCHAR(255) NOT NULL, "
    f"path VARCHAR(255) NOT NULL, fire_at DOUBLE NOT NULL, PRIMARY KEY (execution_id, path)) {_MYSQL_TABLE}",
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_spawns (seq BIGINT AUTO_INCREMENT PRIMARY KEY, "
    f"parent_id VARCHAR(255) NOT NULL, child_id VARCHAR(255) NOT NULL, root_path VARCHAR(255) NOT NULL, "
    f"context LONGTEXT NOT NULL) {_MYSQL_TABLE}",
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_trace (execution_id VARCHAR(255) NOT NULL, idx INT NOT NULL, "
    f"entry LONGTEXT NOT NULL, PRIMARY KEY (execution_id, idx)) {_MYSQL_TABLE}",
)

MYSQL_TRANSPORT = (
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_transport_messages (seq BIGINT AUTO_INCREMENT PRIMARY KEY, "
    f"group_id VARCHAR(255) NOT NULL, event LONGTEXT NOT NULL, "
    f"INDEX {PREFIX}_transport_messages_by_group (group_id, seq)) {_MYSQL_TABLE}",
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_transport_groups (group_id VARCHAR(255) PRIMARY KEY, "
    f"locked_by VARCHAR(255), lock_expiry DOUBLE, priority INT NOT NULL DEFAULT 0, "
    # claim's order (least recently served first), as harel's PostgreSQL schema indexes it
    f"INDEX {PREFIX}_transport_groups_claim_order ((COALESCE(lock_expiry, 0)), group_id)) {_MYSQL_TABLE}",
)


TABLES = (
    f"{PREFIX}_executions",
    f"{PREFIX}_outbox",
    f"{PREFIX}_processed_events",
    f"{PREFIX}_timers",
    f"{PREFIX}_spawns",
    f"{PREFIX}_trace",
    f"{PREFIX}_transport_messages",
    f"{PREFIX}_transport_groups",
)

# the PL/pgSQL functions harel's PostgreSQL backends call, with their signatures for DROP
POSTGRES_FUNCTIONS = (
    f"{PREFIX}_commit_cas(text, text, text, bigint, text)",
    f"{PREFIX}_claim(double precision, double precision, text, int)",
    f"{PREFIX}_ack(text, bigint, text, double precision)",
)

SUPPORTED_VENDORS = ("sqlite", "postgresql", "mysql")


def check_vendor(vendor: str) -> None:
    if vendor not in SUPPORTED_VENDORS:
        raise ImproperlyConfigured(
            f"django-harel supports the {', '.join(SUPPORTED_VENDORS)} database backends, not {vendor!r}"
        )


def create_statements(vendor: str) -> tuple[str, ...]:
    """The statements that create the whole schema on a `vendor` database (Django's
    `connection.vendor`)."""
    check_vendor(vendor)
    if vendor == "sqlite":
        return frozen.SQLITE_STORE + frozen.SQLITE_TRANSPORT
    if vendor == "postgresql":
        return frozen.POSTGRES_STORE + frozen.POSTGRES_TRANSPORT
    return MYSQL_STORE + MYSQL_TRANSPORT


def drop_statements(vendor: str) -> tuple[str, ...]:
    """The statements that remove everything `create_statements(vendor)` creates."""
    check_vendor(vendor)
    tables = tuple(f"DROP TABLE IF EXISTS {t}" for t in TABLES)
    if vendor == "postgresql":
        return tuple(f"DROP FUNCTION IF EXISTS {f}" for f in POSTGRES_FUNCTIONS) + tables
    return tables
