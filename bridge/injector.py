"""Injector: poll Miaoda `commands` table for unconsumed user-submitted commands,
inject them into the corresponding local agent CLI session (via the right
adapter), mark consumed, and write audit records.

Safety:
  - Shell injection: subprocess.run([...], cwd=...) with a LIST, never shell=True.
  - Sender allowlist: reject commands from unauthorized sender_open_id.
  - Idempotent consume: WHERE consumed=FALSE prevents double-execution.
"""
from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import time
from typing import Any

from . import config
from . import lark_db
from . import state
from .agents import get_adapter

log = logging.getLogger(__name__)

POLL_INTERVAL = 2  # seconds between poll loops

_AUDIT_CREATE_SQL = """CREATE TABLE IF NOT EXISTS audit (
    id BIGINT,
    session_id TEXT,
    agent TEXT,
    sender TEXT,
    status TEXT,
    ts TEXT
)"""


def _audit_record(cmd_id: int, session_id: str, agent: str, sender: str, status: str) -> None:
    db_path = state._ensure_db()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = sqlite3.connect(db_path)
    try:
        con.execute(_AUDIT_CREATE_SQL)
        # Migrate old audit tables that predate the `agent` column.
        try:
            con.execute("ALTER TABLE audit ADD COLUMN agent TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        con.execute(
            "INSERT INTO audit (id, session_id, agent, sender, status, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cmd_id, session_id, agent, sender, status, now),
        )
        con.commit()
    finally:
        con.close()


def _consume_command(cmd_id: int, result: str) -> int:
    """Mark a command row consumed (idempotent: WHERE consumed=FALSE)."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sql = (
        f"UPDATE commands SET consumed = TRUE, consumed_at = {lark_db.sql_str(now)}, "
        f"result = {lark_db.sql_str(result)} "
        f"WHERE id = {cmd_id} AND consumed = FALSE"
    )
    return lark_db.execute(sql)


def _process_command(cmd: dict[str, Any]) -> None:
    cmd_id = cmd["id"]
    session_id = cmd["session_id"]
    content = cmd["content"]
    sender = cmd["sender_open_id"]
    agent_key = cmd.get("agent") or "copilot"
    adapter = get_adapter(agent_key)

    # --- Allowlist check ---
    if sender not in config.ALLOWED_OPEN_IDS:
        result = "forbidden: sender not allowed"
        log.warning("command %d rejected: sender %s not in allowlist", cmd_id, sender)
        _audit_record(cmd_id, session_id, agent_key, sender, "forbidden")
        _consume_command(cmd_id, result)
        return

    # --- Online or offline? ---
    try:
        is_online = adapter.is_online(session_id)
    except Exception as exc:
        log.warning("agent %s is_online check failed for %s: %s", agent_key, session_id, exc)
        is_online = False

    if is_online:
        result = adapter.inject_online(session_id, content)
        status = "online_inject"
    else:
        cwd = adapter.get_cwd(session_id) or os.path.expanduser("~")
        if not adapter.get_cwd(session_id):
            log.warning("no cwd for session %s, defaulting to home dir", session_id)
        result = adapter.resume_offline(session_id, content, cwd)
        status = "offline_resume"

    _audit_record(cmd_id, session_id, agent_key, sender, status)
    affected = _consume_command(cmd_id, result)
    if affected == 0:
        log.info("command %d already consumed by another poller (race)", cmd_id)
    else:
        log.info("command %d consumed (agent=%s): %s", cmd_id, agent_key, result[:120])


def poll_once() -> int:
    rows = lark_db.query(
        "SELECT id, session_id, content, sender_open_id, "
        "COALESCE(agent, 'copilot') AS agent "
        "FROM commands WHERE consumed = FALSE ORDER BY id LIMIT 50"
    )
    if not rows:
        log.debug("no unconsumed commands")
        return 0

    log.info("found %d unconsumed commands", len(rows))
    for cmd in rows:
        try:
            _process_command(cmd)
        except Exception as exc:
            log.error("error processing command %s: %s", cmd.get("id"), exc, exc_info=True)
            try:
                _consume_command(cmd["id"], f"error: {exc}")
            except Exception:
                pass
    return len(rows)


# ---------------------------------------------------------------------------
# Rename relay: write page-side renames back to CLI native storage
# ---------------------------------------------------------------------------

def _consume_rename(rid: int, result: str) -> int:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sql = (
        f"UPDATE renames SET consumed = TRUE, consumed_at = {lark_db.sql_str(now)}, "
        f"result = {lark_db.sql_str(result)} "
        f"WHERE id = {rid} AND consumed = FALSE"
    )
    return lark_db.execute(sql)


def poll_renames_once() -> int:
    """Poll renames table; for each, write the name back to the CLI's native
    session storage via the adapter, then mark consumed."""
    rows = lark_db.query(
        "SELECT id, session_id, name, COALESCE(agent, 'copilot') AS agent "
        "FROM renames WHERE consumed = FALSE ORDER BY id LIMIT 50"
    )
    if not rows:
        return 0
    log.info("found %d pending renames", len(rows))
    for r in rows:
        rid = r["id"]
        adapter = get_adapter(r.get("agent") or "copilot")
        try:
            ok = adapter.set_title(r["session_id"], r["name"])
            result = "ok: native title updated" if ok else "noop: adapter did not update"
        except Exception as exc:
            log.error("rename %s failed: %s", rid, exc, exc_info=True)
            result = f"error: {exc}"
        _consume_rename(rid, result)
    return len(rows)


def poll_loop() -> None:
    log.info("injector daemon: starting continuous poll (interval=%ds)", POLL_INTERVAL)
    while True:
        try:
            poll_once()
            poll_renames_once()
        except Exception as exc:
            log.error("poll_loop error: %s", exc, exc_info=True)
        time.sleep(POLL_INTERVAL)
