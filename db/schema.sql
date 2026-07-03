-- AgentCLI Bridge — Miaoda hosted Postgres schema.
-- Run on the DEV database (lark-cli apps +db-execute --env dev); releasing the
-- app migrates these tables to ONLINE (online forbids DDL, allows DML).
-- Idempotent: safe to re-run. Matches app/server/database/schema.ts.

-- Sessions index (one row per local agent session, any agent).
CREATE TABLE IF NOT EXISTS sessions (
  id           TEXT PRIMARY KEY,
  agent        TEXT NOT NULL DEFAULT 'copilot',
  cwd          TEXT,
  summary      TEXT,
  display_name TEXT,
  updated_at   TEXT,
  online       BOOLEAN NOT NULL DEFAULT FALSE,
  pid          INTEGER,
  ctx_used     INTEGER,
  ctx_limit    INTEGER,
  hidden       BOOLEAN NOT NULL DEFAULT FALSE,
  indexed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions (updated_at);
CREATE INDEX IF NOT EXISTS idx_sessions_agent   ON sessions (agent);

-- Conversation event stream (one row = one mapped transcript event).
-- id is a stable 53-bit hash (blake2b) of a per-event key — idempotent inserts.
CREATE TABLE IF NOT EXISTS events (
  id          BIGINT PRIMARY KEY,
  session_id  VARCHAR(64),
  role        VARCHAR(16),
  content     TEXT,
  ts          VARCHAR(32)
);
CREATE INDEX IF NOT EXISTS idx_events_session_ts ON events (session_id, ts);

-- Command queue (frontend writes, bridge consumes + injects into a session).
CREATE TABLE IF NOT EXISTS commands (
  id             BIGINT PRIMARY KEY,
  session_id     TEXT NOT NULL,
  agent          TEXT NOT NULL DEFAULT 'copilot',
  content        TEXT NOT NULL,
  sender_open_id TEXT,
  created_at     TEXT,
  consumed       BOOLEAN NOT NULL DEFAULT FALSE,
  consumed_at    TEXT,
  result         TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_pending ON commands (consumed) WHERE consumed = FALSE;

-- Rename queue (frontend renames a session; bridge writes the name back to the
-- agent's native storage, e.g. Claude custom-title / Copilot session-store).
CREATE TABLE IF NOT EXISTS renames (
  id          BIGINT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  agent       TEXT NOT NULL DEFAULT 'copilot',
  name        TEXT NOT NULL,
  consumed    BOOLEAN NOT NULL DEFAULT FALSE,
  created_at  TEXT,
  consumed_at TEXT,
  result      TEXT
);
