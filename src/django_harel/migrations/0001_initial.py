"""harel's SQL schema, version 6 under the prefix "harel", as harel 0.10 generates it, and the
`HarelExecution` / `HarelMachine` models over it.

The SQLite and PostgreSQL statements are harel's own (`harel.engine.schema.store_schema` /
`transport_schema`), copied here and never edited: a migration must create the same tables
whatever harel version is installed when it runs. `tests/test_schema.py` checks they still match
the installed harel; when harel's `SCHEMA_VERSION` moves on, a new migration takes the database
there. harel has no MySQL backend: on MySQL/MariaDB the tables follow the PostgreSQL layout — the
transport keeps its lease on the group row — and only `DjangoStore` / `DjangoTransport` use them.
"""

from django.db import migrations, models

SCHEMA_VERSION = 6

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

SQLITE_STORE = (
    "CREATE TABLE IF NOT EXISTS harel_executions (id TEXT PRIMARY KEY, definition_id TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, target_id TEXT, event TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_processed_events (execution_id TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY (execution_id, event_id))",
    "CREATE TABLE IF NOT EXISTS harel_timers (execution_id TEXT NOT NULL, path TEXT NOT NULL, fire_at REAL NOT NULL, PRIMARY KEY (execution_id, path))",
    "CREATE TABLE IF NOT EXISTS harel_spawns (seq INTEGER PRIMARY KEY AUTOINCREMENT, parent_id TEXT NOT NULL, child_id TEXT NOT NULL, root_path TEXT NOT NULL, context TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_trace (execution_id TEXT NOT NULL, idx INTEGER NOT NULL, entry TEXT NOT NULL, PRIMARY KEY (execution_id, idx))",
    "CREATE TABLE IF NOT EXISTS harel_definitions (definition_id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL, source TEXT NOT NULL, published_at REAL, PRIMARY KEY (definition_id, version))",
    "CREATE TABLE IF NOT EXISTS harel_workers (worker_id TEXT PRIMARY KEY, record TEXT NOT NULL)",
)

POSTGRES_STORE = (
    "CREATE TABLE IF NOT EXISTS harel_executions (id TEXT PRIMARY KEY, definition_id TEXT NOT NULL, data TEXT NOT NULL, version INT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_outbox (seq BIGSERIAL PRIMARY KEY, target_id TEXT, event TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_processed_events (execution_id TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY (execution_id, event_id))",
    "CREATE TABLE IF NOT EXISTS harel_timers (execution_id TEXT NOT NULL, path TEXT NOT NULL, fire_at DOUBLE PRECISION NOT NULL, PRIMARY KEY (execution_id, path))",
    "CREATE TABLE IF NOT EXISTS harel_spawns (seq BIGSERIAL PRIMARY KEY, parent_id TEXT NOT NULL, child_id TEXT NOT NULL, root_path TEXT NOT NULL, context TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_trace (execution_id TEXT NOT NULL, idx INT NOT NULL, entry TEXT NOT NULL, PRIMARY KEY (execution_id, idx))",
    "CREATE TABLE IF NOT EXISTS harel_definitions (definition_id TEXT NOT NULL, version INT NOT NULL, digest TEXT NOT NULL, source TEXT NOT NULL, published_at DOUBLE PRECISION, PRIMARY KEY (definition_id, version))",
    "CREATE TABLE IF NOT EXISTS harel_workers (worker_id TEXT PRIMARY KEY, record TEXT NOT NULL)",
    "CREATE OR REPLACE FUNCTION harel_commit_cas(p_id text, p_defn text, p_data text, p_old bigint, p_event text)\nRETURNS boolean AS $$\nDECLARE n int; row_exists boolean;\nBEGIN\n  UPDATE harel_executions SET data = p_data, version = p_old + 1 WHERE id = p_id AND version = p_old;\n  GET DIAGNOSTICS n = ROW_COUNT;\n  IF n = 0 THEN\n    SELECT EXISTS (SELECT 1 FROM harel_executions WHERE id = p_id) INTO row_exists;\n    IF p_old = 0 AND NOT row_exists THEN\n      INSERT INTO harel_executions (id, definition_id, data, version) VALUES (p_id, p_defn, p_data, 1);\n    ELSE\n      RETURN false;\n    END IF;\n  END IF;\n  IF p_event <> '' THEN\n    INSERT INTO harel_processed_events (execution_id, event_id) VALUES (p_id, p_event) ON CONFLICT DO NOTHING;\n  END IF;\n  RETURN true;\nEND; $$ LANGUAGE plpgsql",
)

SQLITE_TRANSPORT = (
    "CREATE TABLE IF NOT EXISTS harel_transport_messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT NOT NULL, event TEXT NOT NULL, locked_by TEXT, lock_expiry REAL, published_at REAL)",
    "CREATE TABLE IF NOT EXISTS harel_transport_groups (group_id TEXT PRIMARY KEY, last_claimed_at REAL NOT NULL DEFAULT 0.0, priority INT NOT NULL DEFAULT 0, routing_key TEXT)",
    "CREATE INDEX IF NOT EXISTS harel_transport_groups_by_last_claimed ON harel_transport_groups (last_claimed_at)",
    "CREATE INDEX IF NOT EXISTS harel_transport_messages_by_group ON harel_transport_messages (group_id, seq)",
    "CREATE INDEX IF NOT EXISTS harel_transport_groups_by_routing_key ON harel_transport_groups (routing_key, last_claimed_at)",
)

POSTGRES_TRANSPORT = (
    "CREATE TABLE IF NOT EXISTS harel_transport_messages (seq BIGSERIAL PRIMARY KEY, group_id TEXT NOT NULL, event TEXT NOT NULL, published_at DOUBLE PRECISION)",
    "ALTER TABLE harel_transport_messages ADD COLUMN IF NOT EXISTS published_at DOUBLE PRECISION",
    "CREATE INDEX IF NOT EXISTS harel_transport_messages_by_group ON harel_transport_messages (group_id, seq)",
    "CREATE TABLE IF NOT EXISTS harel_transport_groups (group_id TEXT PRIMARY KEY, locked_by TEXT, lock_expiry DOUBLE PRECISION, priority INT NOT NULL DEFAULT 0, routing_key TEXT)",
    "ALTER TABLE harel_transport_groups ADD COLUMN IF NOT EXISTS routing_key TEXT",
    "CREATE INDEX IF NOT EXISTS harel_transport_groups_by_routing_key ON harel_transport_groups (routing_key, (COALESCE(lock_expiry, 0)), group_id)",
    "CREATE INDEX IF NOT EXISTS harel_transport_groups_claim_order ON harel_transport_groups ((COALESCE(lock_expiry, 0)), group_id)",
    "DROP INDEX IF EXISTS harel_transport_groups_claimable",
    "DROP FUNCTION IF EXISTS harel_claim(double precision, double precision, text, int)",
    "CREATE OR REPLACE FUNCTION harel_claim(p_now double precision, p_lease double precision, p_token text, p_min_priority int DEFAULT 0, p_routing_keys text[] DEFAULT NULL)\nRETURNS TABLE(group_id text, seq bigint, event text) AS $$\nDECLARE g text;\nBEGIN\n  LOOP\n    UPDATE harel_transport_groups tg SET locked_by = p_token, lock_expiry = p_lease\n    WHERE tg.group_id = (\n      SELECT s.group_id FROM harel_transport_groups s\n      WHERE (s.locked_by IS NULL OR s.lock_expiry < p_now)\n        AND s.priority >= p_min_priority\n        AND (p_routing_keys IS NULL OR s.routing_key = ANY(p_routing_keys))\n      ORDER BY COALESCE(s.lock_expiry, 0) ASC, s.group_id FOR UPDATE SKIP LOCKED LIMIT 1\n    ) RETURNING tg.group_id INTO g;\n    IF g IS NULL THEN RETURN; END IF;\n    RETURN QUERY SELECT m.group_id, m.seq, m.event FROM harel_transport_messages m\n                 WHERE m.group_id = g ORDER BY m.seq LIMIT 1;\n    IF FOUND THEN RETURN; END IF;\n    DELETE FROM harel_transport_groups WHERE harel_transport_groups.group_id = g AND locked_by = p_token;\n  END LOOP;\nEND; $$ LANGUAGE plpgsql",
    "CREATE OR REPLACE FUNCTION harel_ack(p_group text, p_seq bigint, p_token text, p_now double precision)\nRETURNS void AS $$\nBEGIN\n  PERFORM 1 FROM harel_transport_groups WHERE group_id = p_group AND locked_by = p_token FOR UPDATE;\n  IF FOUND THEN\n    DELETE FROM harel_transport_messages WHERE seq = p_seq;\n    IF EXISTS (SELECT 1 FROM harel_transport_messages WHERE group_id = p_group) THEN\n      UPDATE harel_transport_groups SET locked_by = NULL, lock_expiry = p_now\n      WHERE group_id = p_group AND locked_by = p_token;\n    ELSE\n      DELETE FROM harel_transport_groups WHERE group_id = p_group AND locked_by = p_token;\n    END IF;\n  END IF;\nEND; $$ LANGUAGE plpgsql",
)

# MySQL: ids are case-sensitive (binary collation), and every indexed text column is a
# VARCHAR — MySQL can't index TEXT. 255 utf8mb4 characters keep each key within InnoDB's
# 3072-byte limit, the two-column ones included.
_MYSQL_TABLE = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"

MYSQL_STORE = (
    "CREATE TABLE IF NOT EXISTS harel_executions (id VARCHAR(255) PRIMARY KEY, "
    f"definition_id VARCHAR(255) NOT NULL, data LONGTEXT NOT NULL, version INT NOT NULL) {_MYSQL_TABLE}",
    "CREATE TABLE IF NOT EXISTS harel_outbox (seq BIGINT AUTO_INCREMENT PRIMARY KEY, "
    f"target_id VARCHAR(255), event LONGTEXT NOT NULL) {_MYSQL_TABLE}",
    "CREATE TABLE IF NOT EXISTS harel_processed_events (execution_id VARCHAR(255) NOT NULL, "
    f"event_id VARCHAR(255) NOT NULL, PRIMARY KEY (execution_id, event_id)) {_MYSQL_TABLE}",
    "CREATE TABLE IF NOT EXISTS harel_timers (execution_id VARCHAR(255) NOT NULL, "
    f"path VARCHAR(255) NOT NULL, fire_at DOUBLE NOT NULL, PRIMARY KEY (execution_id, path)) {_MYSQL_TABLE}",
    "CREATE TABLE IF NOT EXISTS harel_spawns (seq BIGINT AUTO_INCREMENT PRIMARY KEY, "
    "parent_id VARCHAR(255) NOT NULL, child_id VARCHAR(255) NOT NULL, root_path VARCHAR(255) NOT NULL, "
    f"context LONGTEXT NOT NULL) {_MYSQL_TABLE}",
    "CREATE TABLE IF NOT EXISTS harel_trace (execution_id VARCHAR(255) NOT NULL, idx INT NOT NULL, "
    f"entry LONGTEXT NOT NULL, PRIMARY KEY (execution_id, idx)) {_MYSQL_TABLE}",
    "CREATE TABLE IF NOT EXISTS harel_definitions (definition_id VARCHAR(255) NOT NULL, "
    "version INT NOT NULL, digest VARCHAR(255) NOT NULL, source LONGTEXT NOT NULL, "
    f"published_at DOUBLE, PRIMARY KEY (definition_id, version)) {_MYSQL_TABLE}",
    "CREATE TABLE IF NOT EXISTS harel_workers (worker_id VARCHAR(255) PRIMARY KEY, "
    f"record LONGTEXT NOT NULL) {_MYSQL_TABLE}",
)

MYSQL_TRANSPORT = (
    "CREATE TABLE IF NOT EXISTS harel_transport_messages (seq BIGINT AUTO_INCREMENT PRIMARY KEY, "
    "group_id VARCHAR(255) NOT NULL, event LONGTEXT NOT NULL, published_at DOUBLE, "
    f"INDEX harel_transport_messages_by_group (group_id, seq)) {_MYSQL_TABLE}",
    "CREATE TABLE IF NOT EXISTS harel_transport_groups (group_id VARCHAR(255) PRIMARY KEY, "
    "locked_by VARCHAR(255), lock_expiry DOUBLE, priority INT NOT NULL DEFAULT 0, "
    "routing_key VARCHAR(255), "
    # claim's order (least recently served first), as harel's PostgreSQL schema indexes it
    "INDEX harel_transport_groups_claim_order ((COALESCE(lock_expiry, 0)), group_id), "
    # a worker given `--machines` claims in this order among its machines' groups
    "INDEX harel_transport_groups_by_routing_key (routing_key, (COALESCE(lock_expiry, 0)), group_id)"
    f") {_MYSQL_TABLE}",
)

# the PL/pgSQL functions harel's PostgreSQL backends call, with their signatures for DROP
POSTGRES_FUNCTIONS = (
    "harel_commit_cas(text, text, text, bigint, text)",
    "harel_claim(double precision, double precision, text, int, text[])",
    "harel_ack(text, bigint, text, double precision)",
)


def _statements(vendor: str) -> tuple[str, ...]:
    if vendor == "sqlite":
        return SQLITE_STORE + SQLITE_TRANSPORT
    if vendor == "postgresql":
        return POSTGRES_STORE + POSTGRES_TRANSPORT
    return MYSQL_STORE + MYSQL_TRANSPORT


def create_schema(apps, schema_editor):
    for statement in _statements(schema_editor.connection.vendor):
        schema_editor.execute(statement, params=None)


def drop_schema(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        for function in POSTGRES_FUNCTIONS:
            schema_editor.execute(f"DROP FUNCTION IF EXISTS {function}", params=None)
    for table in TABLES:
        schema_editor.execute(f"DROP TABLE IF EXISTS {table}", params=None)


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        # MySQL can't roll DDL back, and Django refuses DDL inside a transaction there
        migrations.RunPython(create_schema, drop_schema, atomic=False),
        migrations.CreateModel(
            name="HarelExecution",
            fields=[
                ("id", models.CharField(max_length=255, primary_key=True, serialize=False)),
                ("definition_id", models.CharField(max_length=255)),
                ("data", models.TextField()),
                ("version", models.IntegerField()),
            ],
            options={
                "verbose_name": "execution",
                "db_table": "harel_executions",
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="HarelMachine",
            fields=[],
            options={
                "verbose_name": "machine",
                "verbose_name_plural": "machines",
                "proxy": True,
                "default_permissions": (),
                "indexes": [],
                "constraints": [],
            },
            bases=("django_harel.harelexecution",),
        ),
    ]
