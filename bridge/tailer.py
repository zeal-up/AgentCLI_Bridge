"""Tailer: read append-only event logs from every agent adapter, map events to
(role, content) rows, and batch-INSERT into the Miaoda `events` table.

The byte-reading / batching / offset-persistence core is agent-agnostic; only
the event-log path, event mapping, timestamp, and dedup key differ, all
delegated to the adapter.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

from . import lark_db
from . import state
from .agents import AGENTS, adapter_for_session, AgentAdapter

log = logging.getLogger(__name__)

_FLUSH_ROW_THRESHOLD = 200
_FLUSH_SEC_THRESHOLD = 1.0


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _gen_id(stable_key: str) -> int:
    """Stable, JS-safe (<=2^53) BIGINT from a stable key string.

    Idempotent across re-runs: the same event always maps to the same key, so
    ON CONFLICT (id) DO NOTHING dedups even if the byte offset is lost.
    """
    h = hashlib.blake2b(stable_key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big") & ((1 << 53) - 1)


# ---------------------------------------------------------------------------
# Batch flush
# ---------------------------------------------------------------------------

def _flush_rows(rows: list[tuple[int, str, str, str, str]]) -> int:
    if not rows:
        return 0
    values_list = [
        f"({lark_db.sql_str(eid)}, {lark_db.sql_str(sid)}, "
        f"{lark_db.sql_str(role)}, {lark_db.sql_str(content)}, {lark_db.sql_str(ts)})"
        for (eid, sid, role, content, ts) in rows
    ]
    sql = (
        "INSERT INTO events (id, session_id, role, content, ts) VALUES "
        + ", ".join(values_list)
        + " ON CONFLICT (id) DO NOTHING"
    )
    return lark_db.execute(sql)


# ---------------------------------------------------------------------------
# Session tailer (adapter-driven)
# ---------------------------------------------------------------------------

def tail_session(session_id: str, once: bool = False, adapter: AgentAdapter | None = None) -> int:
    """Tail one session's event log via its adapter. If *once*, process
    available bytes and return; otherwise loop. Returns rows inserted (call)."""
    if adapter is None:
        adapter = adapter_for_session(session_id)
        if adapter is None:
            log.warning("no adapter owns session %s", session_id)
            return 0

    fpath = adapter.events_path(session_id)
    if not fpath or not os.path.exists(fpath):
        log.warning("event log not found for session %s (%s)", session_id, fpath)
        return 0

    offset = state.get_offset(session_id)
    file_size = os.path.getsize(fpath)
    if file_size < offset:
        log.info("session %s: file truncated (size=%d < offset=%d), resetting",
                 session_id, file_size, offset)
        offset = 0
        state.set_offset(session_id, 0)

    pending_rows: list[tuple[int, str, str, str, str]] = []
    batch_time = time.time()
    total_inserted = 0
    partial_line = ""
    ctx = adapter.new_ctx()

    def _do_flush() -> None:
        nonlocal pending_rows, batch_time, total_inserted
        if not pending_rows:
            return
        _flush_rows(pending_rows)
        total_inserted += len(pending_rows)
        log.debug("flushed %d rows for session %s", len(pending_rows), session_id)
        pending_rows = []
        batch_time = time.time()
        state.set_offset(session_id, offset)

    fp = open(fpath, "rb")
    fp.seek(offset)
    try:
        while True:
            chunk = fp.read(64 * 1024)
            if chunk:
                offset += len(chunk)
                text = chunk.decode("utf-8", errors="replace")
                if partial_line:
                    text = partial_line + text
                lines = text.split("\n")
                partial_line = lines[-1]
                full_lines = lines[:-1]

                for line in full_lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("session %s: skip non-JSON line: %s",
                                    session_id, line[:120])
                        continue

                    rows = adapter.map_event(event, ctx)
                    ts = adapter.event_ts(event)
                    base_key = adapter.stable_key(session_id, event)
                    for i, (role, content) in enumerate(rows):
                        # Sub-index rows beyond the first so multiple rows from
                        # one event get distinct (but stable) ids. The first
                        # row keeps the bare key (preserves Copilot's existing
                        # id hashes for idempotency).
                        key = base_key if i == 0 else f"{base_key}\x1f#{i}"
                        eid = _gen_id(key)
                        pending_rows.append((eid, session_id, role, content, ts))

                    # Turn-complete marker: emit a synthetic system row so the
                    # page's send-lock can release on a real signal instead of
                    # guessing from transcript content. Checked even when
                    # map_event returned no rows (e.g. Copilot's turn_end is a
                    # skip-type for display but still a completion signal).
                    if adapter.is_turn_complete(event):
                        mkey = f"{base_key}\x1fdone"
                        pending_rows.append(
                            (_gen_id(mkey), session_id, "system",
                             "✓ turn complete", ts)
                        )

                    if len(pending_rows) >= _FLUSH_ROW_THRESHOLD:
                        _do_flush()

                if pending_rows and (time.time() - batch_time) >= _FLUSH_SEC_THRESHOLD:
                    _do_flush()

            else:
                if pending_rows:
                    _do_flush()
                if once:
                    break
                time.sleep(1.0)
                new_size = os.path.getsize(fpath)
                if new_size < offset:
                    log.info("session %s: truncated during tail, resetting", session_id)
                    offset = 0
                    state.set_offset(session_id, 0)
                    fp.seek(0)
    except KeyboardInterrupt:
        log.info("session %s: interrupted, flushing", session_id)
        if pending_rows:
            _do_flush()
    finally:
        fp.close()

    if pending_rows:
        _do_flush()

    log.info("session %s: inserted %d event rows", session_id, total_inserted)
    return total_inserted


# ---------------------------------------------------------------------------
# All-session tailer (all adapters)
# ---------------------------------------------------------------------------

def tail_all(once: bool = False) -> int:
    """Tail all discovered sessions across all adapters."""
    total = 0
    if once:
        for adapter in AGENTS:
            for sid in adapter.discover_tail_ids():
                total += tail_session(sid, once=True, adapter=adapter)
        log.info("tail_all once: %d rows across all agents", total)
        return total

    log.info("tail_all daemon: starting continuous tail across all agents")
    while True:
        any_new = False
        for adapter in AGENTS:
            for sid in adapter.discover_tail_ids():
                n = tail_session(sid, once=True, adapter=adapter)
                if n > 0:
                    any_new = True
                total += n
        if not any_new:
            time.sleep(1.0)
