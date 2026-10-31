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
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_definitions (definition_id VARCHAR(255) NOT NULL, "
    f"version INT NOT NULL, digest VARCHAR(255) NOT NULL, source LONGTEXT NOT NULL, "
    f"published_at DOUBLE, PRIMARY KEY (definition_id, version)) {_MYSQL_TABLE}",
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_workers (worker_id VARCHAR(255) PRIMARY KEY, "
    f"record LONGTEXT NOT NULL) {_MYSQL_TABLE}",
)

MYSQL_TRANSPORT = (
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_transport_messages (seq BIGINT AUTO_INCREMENT PRIMARY KEY, "
    f"group_id VARCHAR(255) NOT NULL, event LONGTEXT NOT NULL, published_at DOUBLE, "
    f"INDEX {PREFIX}_transport_messages_by_group (group_id, seq)) {_MYSQL_TABLE}",
    f"CREATE TABLE IF NOT EXISTS {PREFIX}_transport_groups (group_id VARCHAR(255) PRIMARY KEY, "
    f"locked_by VARCHAR(255), lock_expiry DOUBLE, priority INT NOT NULL DEFAULT 0, "
    f"routing_key VARCHAR(255), "
    # claim's order (least recently served first), as harel's PostgreSQL schema indexes it
    f"INDEX {PREFIX}_transport_groups_claim_order ((COALESCE(lock_expiry, 0)), group_id), "
    # a worker given `--machines` claims in this order among its machines' groups
    f"INDEX {PREFIX}_transport_groups_by_routing_key (routing_key, (COALESCE(lock_expiry, 0)), group_id)"
    f") {_MYSQL_TABLE}",
)


TABLES = (
    f"{PREFIX}_executions",
    f"{PREFIX}_outbox",
    f"{PREFIX}_processed_events",
    f"{PREFIX}_timers",
    f"{PREFIX}_spawns",
    f"{PREFIX}_trace",
    f"{PREFIX}_definitions",
    f"{PREFIX}_workers",
    f"{PREFIX}_transport_messages",
    f"{PREFIX}_transport_groups",
)

# the one statement of {STORE} that creates {PREFIX}_definitions, for a migration that adds it
# to a database that already has the rest (SCHEMA_VERSION 3 -> 4) — found rather than
# hand-transcribed, so it can never drift from the frozen copy
DEFINITIONS_TABLE = f"{PREFIX}_definitions"


def definitions_create_statement(vendor: str) -> str:
    check_vendor(vendor)
    return _store_create_statement(vendor, DEFINITIONS_TABLE)


def _store_create_statement(vendor: str, table: str) -> str:
    """The statement of the frozen (or MySQL) store schema that creates `table`."""
    store = {"sqlite": frozen.SQLITE_STORE, "postgresql": frozen.POSTGRES_STORE}.get(vendor, MYSQL_STORE)
    return next(s for s in store if s.startswith(f"CREATE TABLE IF NOT EXISTS {table} "))


def definitions_drop_statement() -> str:
    return f"DROP TABLE IF EXISTS {DEFINITIONS_TABLE}"


# the PL/pgSQL functions harel's PostgreSQL backends call, with their signatures for DROP
POSTGRES_FUNCTIONS = (
    f"{PREFIX}_commit_cas(text, text, text, bigint, text)",
    f"{PREFIX}_claim(double precision, double precision, text, int, text[])",
    f"{PREFIX}_ack(text, bigint, text, double precision)",
)


# the statements that bring {PREFIX}_transport_groups up to SCHEMA_VERSION 5 (routing_key) on a
# database that already has the rest — found from the frozen copy where harel's schema has them;
# SQLite's own `ALTER` isn't there (harel adds that column itself, conditionally, the moment its
# own transport starts, since SQLite has no `ADD COLUMN IF NOT EXISTS`) — a migration only runs
# once, so the plain, unconditional form is fine
def routing_key_statements(vendor: str) -> tuple[str, ...]:
    check_vendor(vendor)
    if vendor == "sqlite":
        index = next(
            s for s in frozen.SQLITE_TRANSPORT if s.startswith("CREATE INDEX") and "routing_key" in s
        )
        return (f"ALTER TABLE {PREFIX}_transport_groups ADD COLUMN routing_key TEXT", index)
    if vendor == "postgresql":
        # "harel_claim(" catches the DROP (of the pre-routing 4-argument overload) too — it
        # doesn't contain "FUNCTION harel_claim" contiguously ("FUNCTION IF EXISTS harel_claim")
        return tuple(s for s in frozen.POSTGRES_TRANSPORT if "routing_key" in s or "harel_claim(" in s)
    return (
        f"ALTER TABLE {PREFIX}_transport_groups ADD COLUMN routing_key VARCHAR(255)",
        f"CREATE INDEX {PREFIX}_transport_groups_by_routing_key ON {PREFIX}_transport_groups "
        "(routing_key, (COALESCE(lock_expiry, 0)), group_id)",
    )


def routing_key_drop_statements(vendor: str) -> tuple[str, ...]:
    """Reverse of `routing_key_statements`. Leaves PostgreSQL's `harel_claim` at its 5-argument
    form rather than recreating the pre-routing one: a 4-argument call still resolves to it
    (`p_min_priority`/`p_routing_keys` default), so nothing that worked before breaks."""
    check_vendor(vendor)
    index = f"{PREFIX}_transport_groups_by_routing_key"
    table = f"{PREFIX}_transport_groups"
    if vendor == "postgresql":
        return (f"DROP INDEX IF EXISTS {index}", f"ALTER TABLE {table} DROP COLUMN IF EXISTS routing_key")
    if vendor == "sqlite":
        return (f"DROP INDEX IF EXISTS {index}", f"ALTER TABLE {table} DROP COLUMN routing_key")
    return (f"ALTER TABLE {table} DROP INDEX {index}", f"ALTER TABLE {table} DROP COLUMN routing_key")


# SCHEMA_VERSION 5 -> 6, on a database that already has the rest: the workers' heartbeats
# (`{PREFIX}_workers`, which `harel_worker` records) and when each queued message was published
# (`{PREFIX}_transport_messages.published_at`, read by `DjangoTransport.backlog`) — found from the
# frozen copy where harel's schema has them. PostgreSQL's is an `ADD COLUMN IF NOT EXISTS`; SQLite
# and MySQL have no such form, so the migration checks the column first
WORKERS_TABLE = f"{PREFIX}_workers"
MESSAGES_TABLE = f"{PREFIX}_transport_messages"


def workers_create_statement(vendor: str) -> str:
    check_vendor(vendor)
    return _store_create_statement(vendor, WORKERS_TABLE)


def workers_drop_statement() -> str:
    return f"DROP TABLE IF EXISTS {WORKERS_TABLE}"


def published_at_statement(vendor: str) -> str:
    check_vendor(vendor)
    if vendor == "postgresql":
        return next(s for s in frozen.POSTGRES_TRANSPORT if s.startswith("ALTER") and "published_at" in s)
    kind = "REAL" if vendor == "sqlite" else "DOUBLE"
    return f"ALTER TABLE {MESSAGES_TABLE} ADD COLUMN published_at {kind}"


def published_at_drop_statement() -> str:
    return f"ALTER TABLE {MESSAGES_TABLE} DROP COLUMN published_at"


# django-harel's own addition, outside harel's schema: when each queued message was published,
# so the admin dashboard can show how long the oldest one has waited (harel's own schema has no
# timestamp on a message). Keyed by the message's `seq`, written by `DjangoTransport.publish()`
# and dropped by `ack()` along with the message — so a row's absence means either the message
# was never tracked (published before this table existed) or it's gone.
QUEUE_LOG = "django_harel_queue_log"

_QUEUE_LOG_SQLITE = (
    f"CREATE TABLE IF NOT EXISTS {QUEUE_LOG} (seq INTEGER PRIMARY KEY, published_at REAL NOT NULL)"
)
_QUEUE_LOG_POSTGRES = (
    f"CREATE TABLE IF NOT EXISTS {QUEUE_LOG} (seq BIGINT PRIMARY KEY, published_at DOUBLE PRECISION NOT NULL)"
)
_QUEUE_LOG_MYSQL = f"CREATE TABLE IF NOT EXISTS {QUEUE_LOG} (seq BIGINT PRIMARY KEY, published_at DOUBLE NOT NULL) {_MYSQL_TABLE}"


def queue_log_create_statement(vendor: str) -> str:
    check_vendor(vendor)
    if vendor == "sqlite":
        return _QUEUE_LOG_SQLITE
    if vendor == "postgresql":
        return _QUEUE_LOG_POSTGRES
    return _QUEUE_LOG_MYSQL


def queue_log_drop_statement() -> str:
    return f"DROP TABLE IF EXISTS {QUEUE_LOG}"


# `TABLES` plus django-harel's own `QUEUE_LOG` — every table a test or a fixture should empty
# between runs
ALL_TABLES = TABLES + (QUEUE_LOG,)


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
