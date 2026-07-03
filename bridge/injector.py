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
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from typing import Any

from . import config
from . import lark_db
from . import state
from .agents import get_adapter

log = logging.getLogger(__name__)

POLL_INTERVAL = 2  # seconds between poll loops
PROMPT_CHECK_INTERVAL = 5  # seconds between prompt-capture sweeps
PROMPT_RECENT_SEC = 90  # a command consumed within this window = "active turn"
_PROMPT_LAST_CAPTURE_TS: float = 0.0

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
            poll_prompts()
        except Exception as exc:
            log.error("poll_loop error: %s", exc, exc_info=True)
        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Prompt surfacing: when a live agent is blocked on an interactive prompt
# (permission / picker) the prompt is only in the terminal, not the transcript.
# Capture the tmux pane for active turns and surface a system event so the
# page can show the options + unfreeze Send for the user to respond.
# ---------------------------------------------------------------------------

_PROMPT_RE = re.compile(
    r"(?:"
    r"[\?？]\s*\(?[yYnN]\s*/\s*[yYnN]\)?\s*$"   # ...? (y/N)
    r"|[\?？]\s*$"                                # ends with `?`
    r"|>\s*$"                                     # picker prompt `> `
    r"|[:：]\s*$"                                 # `prompt:`
    r"|\(\s*[0-9]+\s*\)\s*$"                     # `(1)` standalone numbered choice
    r"|^\s*[\(\[]?[0-9]+[\)\].:]\s+\S"           # `1) foo` / `[1] foo` numbered option line
    r"|(?:allow|approve|permission|允许|选择|选项|确认|是否|y/n|Y/n)\b"
    r")",
    re.IGNORECASE,
)


def _capture_pane(pane: str, lines: int = 20) -> list[str]:
    try:
        out = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", pane, "-S", f"-{lines}"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def _looks_like_prompt(lines: list[str]) -> bool:
    tail = [ln.strip() for ln in lines[-6:] if ln.strip()]
    for ln in tail:
        if _PROMPT_RE.search(ln):
            return True
    return False


def poll_prompts() -> None:
    """For sessions with a command consumed recently (active turn), if the live
    terminal is showing an interactive prompt, surface its last lines as a
    `system` event so the page can display it + unfreeze Send."""
    global _PROMPT_LAST_CAPTURE_TS
    now = time.time()
    if now - _PROMPT_LAST_CAPTURE_TS < PROMPT_CHECK_INTERVAL:
        return
    _PROMPT_LAST_CAPTURE_TS = now

    since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # consumed_at > (now - PROMPT_RECENT_SEC): active turns
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(seconds=PROMPT_RECENT_SEC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        rows = lark_db.query(
            f"SELECT DISTINCT session_id, COALESCE(agent,'copilot') AS agent "
            f"FROM commands WHERE consumed=TRUE AND consumed_at > {lark_db.sql_str(cutoff)}"
        )
    except Exception as exc:
        log.warning("poll_prompts query failed: %s", exc)
        return

    for r in rows:
        sid = r["session_id"]
        adapter = get_adapter(r.get("agent") or "copilot")
        try:
            pane = adapter.live_pane(sid)
        except Exception:
            pane = None
        if not pane:
            continue
        lines = _capture_pane(pane)
        if not lines or not _looks_like_prompt(lines):
            continue
        body = "📋 terminal waiting for input:\n" + "\n".join(lines[-8:])
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Dedup per session per minute: same prompt within 60s won't re-write.
        eid = int.from_bytes(
            hashlib.blake2b(f"prompt\x1f{sid}\x1f{ts[:16]}".encode(), digest_size=8).digest(),
            "big",
        ) & ((1 << 53) - 1)
        try:
            lark_db.execute(
                "INSERT INTO events (id, session_id, role, content, ts) VALUES "
                f"({lark_db.sql_str(eid)}, {lark_db.sql_str(sid)}, 'system', "
                f"{lark_db.sql_str(body)}, {lark_db.sql_str(ts)}) ON CONFLICT (id) DO NOTHING"
            )
        except Exception as exc:
            log.warning("poll_prompts insert failed for %s: %s", sid, exc)

