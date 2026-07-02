"""Local offset persistence for tailer. Uses a sqlite DB at config.BRIDGE_STATE_DB
to track how many bytes of each session's events.jsonl have been consumed."""
from __future__ import annotations

import logging
import os
import sqlite3

from . import config

log = logging.getLogger(__name__)

_CREATE_SQL = """CREATE TABLE IF NOT EXISTS offsets (
    session_id TEXT PRIMARY KEY,
    byte_offset INTEGER NOT NULL
)"""


def _ensure_db() -> str:
    """Lazily create the bridge-state directory and sqlite file."""
    db_path = config.BRIDGE_STATE_DB
    db_dir = os.path.dirname(db_path)
    if not os.path.isdir(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        log.info("created bridge-state dir: %s", db_dir)
    return db_path


def get_offset(session_id: str) -> int:
    """Return the stored byte offset for *session_id*, or 0 if absent."""
    db_path = _ensure_db()
    con = sqlite3.connect(db_path)
    try:
        con.execute(_CREATE_SQL)
        row = con.execute(
            "SELECT byte_offset FROM offsets WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row else 0
    finally:
        con.close()


def set_offset(session_id: str, byte_offset: int) -> None:
    """Persist *byte_offset* for *session_id*."""
    db_path = _ensure_db()
    con = sqlite3.connect(db_path)
    try:
        con.execute(_CREATE_SQL)
        con.execute(
            "INSERT INTO offsets (session_id, byte_offset) VALUES (?, ?) "
            "ON CONFLICT (session_id) DO UPDATE SET byte_offset = excluded.byte_offset",
            (session_id, byte_offset),
        )
        con.commit()
    finally:
        con.close()
