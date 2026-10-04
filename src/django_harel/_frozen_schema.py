"""harel's SQL schema version 1 under the prefix "harel", frozen as harel 0.7 generates it.

Generated from `harel.engine.schema` (`store_schema` / `transport_schema`) and never edited:
a migration must create the same tables whatever harel version is installed when it runs.
`tests/test_schema.py` checks the copy still matches the installed harel.
"""

SQLITE_STORE = (
    "CREATE TABLE IF NOT EXISTS harel_executions (id TEXT PRIMARY KEY, definition_id TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, target_id TEXT, event TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_processed_events (execution_id TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY (execution_id, event_id))",
    "CREATE TABLE IF NOT EXISTS harel_timers (execution_id TEXT NOT NULL, path TEXT NOT NULL, fire_at REAL NOT NULL, PRIMARY KEY (execution_id, path))",
    "CREATE TABLE IF NOT EXISTS harel_spawns (seq INTEGER PRIMARY KEY AUTOINCREMENT, parent_id TEXT NOT NULL, child_id TEXT NOT NULL, root_path TEXT NOT NULL, context TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_trace (execution_id TEXT NOT NULL, idx INTEGER NOT NULL, entry TEXT NOT NULL, PRIMARY KEY (execution_id, idx))",
)

POSTGRES_STORE = (
    "CREATE TABLE IF NOT EXISTS harel_executions (id TEXT PRIMARY KEY, definition_id TEXT NOT NULL, data TEXT NOT NULL, version INT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_outbox (seq BIGSERIAL PRIMARY KEY, target_id TEXT, event TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_processed_events (execution_id TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY (execution_id, event_id))",
    "CREATE TABLE IF NOT EXISTS harel_timers (execution_id TEXT NOT NULL, path TEXT NOT NULL, fire_at DOUBLE PRECISION NOT NULL, PRIMARY KEY (execution_id, path))",
    "CREATE TABLE IF NOT EXISTS harel_spawns (seq BIGSERIAL PRIMARY KEY, parent_id TEXT NOT NULL, child_id TEXT NOT NULL, root_path TEXT NOT NULL, context TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS harel_trace (execution_id TEXT NOT NULL, idx INT NOT NULL, entry TEXT NOT NULL, PRIMARY KEY (execution_id, idx))",
    "CREATE OR REPLACE FUNCTION harel_commit_cas(p_id text, p_defn text, p_data text, p_old bigint, p_event text)\nRETURNS boolean AS $$\nDECLARE n int; row_exists boolean;\nBEGIN\n  UPDATE harel_executions SET data = p_data, version = p_old + 1 WHERE id = p_id AND version = p_old;\n  GET DIAGNOSTICS n = ROW_COUNT;\n  IF n = 0 THEN\n    SELECT EXISTS (SELECT 1 FROM harel_executions WHERE id = p_id) INTO row_exists;\n    IF p_old = 0 AND NOT row_exists THEN\n      INSERT INTO harel_executions (id, definition_id, data, version) VALUES (p_id, p_defn, p_data, 1);\n    ELSE\n      RETURN false;\n    END IF;\n  END IF;\n  IF p_event <> '' THEN\n    INSERT INTO harel_processed_events (execution_id, event_id) VALUES (p_id, p_event) ON CONFLICT DO NOTHING;\n  END IF;\n  RETURN true;\nEND; $$ LANGUAGE plpgsql",
)

SQLITE_TRANSPORT = (
    "CREATE TABLE IF NOT EXISTS harel_transport_messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT NOT NULL, event TEXT NOT NULL, locked_by TEXT, lock_expiry REAL)",
    "CREATE TABLE IF NOT EXISTS harel_transport_groups (group_id TEXT PRIMARY KEY, last_claimed_at REAL NOT NULL DEFAULT 0.0, priority INT NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS harel_transport_groups_by_last_claimed ON harel_transport_groups (last_claimed_at)",
    "CREATE INDEX IF NOT EXISTS harel_transport_messages_by_group ON harel_transport_messages (group_id, seq)",
)

POSTGRES_TRANSPORT = (
    "CREATE TABLE IF NOT EXISTS harel_transport_messages (seq BIGSERIAL PRIMARY KEY, group_id TEXT NOT NULL, event TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS harel_transport_messages_by_group ON harel_transport_messages (group_id, seq)",
    "CREATE TABLE IF NOT EXISTS harel_transport_groups (group_id TEXT PRIMARY KEY, locked_by TEXT, lock_expiry DOUBLE PRECISION, priority INT NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS harel_transport_groups_claimable ON harel_transport_groups (lock_expiry)",
    "CREATE OR REPLACE FUNCTION harel_claim(p_now double precision, p_lease double precision, p_token text, p_min_priority int DEFAULT 0)\nRETURNS TABLE(group_id text, seq bigint, event text) AS $$\nDECLARE g text;\nBEGIN\n  LOOP\n    UPDATE harel_transport_groups tg SET locked_by = p_token, lock_expiry = p_lease\n    WHERE tg.group_id = (\n      SELECT s.group_id FROM harel_transport_groups s\n      WHERE (s.locked_by IS NULL OR s.lock_expiry < p_now)\n        AND s.priority >= p_min_priority\n      ORDER BY COALESCE(s.lock_expiry, 0) ASC, s.group_id FOR UPDATE SKIP LOCKED LIMIT 1\n    ) RETURNING tg.group_id INTO g;\n    IF g IS NULL THEN RETURN; END IF;\n    RETURN QUERY SELECT m.group_id, m.seq, m.event FROM harel_transport_messages m\n                 WHERE m.group_id = g ORDER BY m.seq LIMIT 1;\n    IF FOUND THEN RETURN; END IF;\n    DELETE FROM harel_transport_groups WHERE harel_transport_groups.group_id = g AND locked_by = p_token;\n  END LOOP;\nEND; $$ LANGUAGE plpgsql",
    "CREATE OR REPLACE FUNCTION harel_ack(p_group text, p_seq bigint, p_token text, p_now double precision)\nRETURNS void AS $$\nBEGIN\n  IF EXISTS (SELECT 1 FROM harel_transport_groups WHERE group_id = p_group AND locked_by = p_token) THEN\n    DELETE FROM harel_transport_messages WHERE seq = p_seq;\n    IF EXISTS (SELECT 1 FROM harel_transport_messages WHERE group_id = p_group) THEN\n      UPDATE harel_transport_groups SET locked_by = NULL, lock_expiry = p_now\n      WHERE group_id = p_group AND locked_by = p_token;\n    ELSE\n      DELETE FROM harel_transport_groups WHERE group_id = p_group AND locked_by = p_token;\n    END IF;\n  END IF;\nEND; $$ LANGUAGE plpgsql",
)
