"""Maintenance helpers for redacting already-mirrored Miaoda rows."""
from __future__ import annotations

import logging
from typing import Any

from . import lark_db
from .redact import redact_text

log = logging.getLogger(__name__)

_UPDATE_CHUNK = 5

_SECRET_REGEXES = (
    r"sk-(proj-)?[A-Za-z0-9_-]{16,}",
    r"(AKIA|ASIA)[0-9A-Z]{16}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"gh[opsru]_[A-Za-z0-9_]{20,}",
    r"glpat-[A-Za-z0-9_-]{20,}",
    r"xox[baprs]-[A-Za-z0-9-]{20,}",
    r"AIza[0-9A-Za-z_-]{20,}",
    r"Bearer\s+[A-Za-z0-9._~+/\-]+=*",
)
_PERSONAL_MARKERS = ("@",)


def _maybe_clause(column: str, *, include_personal: bool = False) -> str:
    parts: list[str] = []
    for pattern in _SECRET_REGEXES:
        parts.append(f"{column} ~* {lark_db.sql_str(pattern)}")
    if include_personal:
        for marker in _PERSONAL_MARKERS:
            value = lark_db.sql_str(marker.lower())
            parts.append(f"POSITION({value} IN LOWER({column})) > 0")
    return f"({column} IS NOT NULL AND ({' OR '.join(parts)}))"


def _event_id(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return lark_db.sql_str(value)


def _chunks(items: list[dict[str, Any]], size: int = _UPDATE_CHUNK) -> list[list[dict[str, Any]]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _update_sessions(rows: list[dict[str, Any]]) -> int:
    updates = [
        {
            "id": row.get("id"),
            "cwd": redact_text(row.get("cwd")),
            "summary": redact_text(row.get("summary")),
        }
        for row in rows
        if redact_text(row.get("cwd")) != row.get("cwd")
        or redact_text(row.get("summary")) != row.get("summary")
    ]
    for chunk in _chunks(updates):
        ids = ", ".join(lark_db.sql_str(row["id"]) for row in chunk)
        cwd_cases = " ".join(
            f"WHEN {lark_db.sql_str(row['id'])} THEN {lark_db.sql_str(row['cwd'])}"
            for row in chunk
        )
        summary_cases = " ".join(
            f"WHEN {lark_db.sql_str(row['id'])} THEN {lark_db.sql_str(row['summary'])}"
            for row in chunk
        )
        lark_db.execute(
            "UPDATE sessions SET "
            f"cwd = CASE id {cwd_cases} ELSE cwd END, "
            f"summary = CASE id {summary_cases} ELSE summary END "
            f"WHERE id IN ({ids})"
        )
    return len(updates)


def _update_events(rows: list[dict[str, Any]]) -> int:
    updates = [
        {
            "id": row.get("id"),
            "content": redact_text(row.get("content"), include_personal=False),
        }
        for row in rows
        if redact_text(row.get("content"), include_personal=False) != row.get("content")
    ]
    for chunk in _chunks(updates):
        ids = ", ".join(_event_id(row["id"]) for row in chunk)
        cases = " ".join(
            f"WHEN {_event_id(row['id'])} THEN {lark_db.sql_str(row['content'])}"
            for row in chunk
        )
        lark_db.execute(
            f"UPDATE events SET content = CASE id {cases} ELSE content END "
            f"WHERE id IN ({ids})"
        )
    return len(updates)


def _update_commands(rows: list[dict[str, Any]]) -> int:
    updates = [
        {
            "id": row.get("id"),
            "content": redact_text(row.get("content"), include_personal=False),
            "result": redact_text(row.get("result"), include_personal=False),
        }
        for row in rows
        if redact_text(row.get("content"), include_personal=False) != row.get("content")
        or redact_text(row.get("result"), include_personal=False) != row.get("result")
    ]
    for chunk in _chunks(updates):
        ids = ", ".join(_event_id(row["id"]) for row in chunk)
        content_cases = " ".join(
            f"WHEN {_event_id(row['id'])} THEN {lark_db.sql_str(row['content'])}"
            for row in chunk
        )
        result_cases = " ".join(
            f"WHEN {_event_id(row['id'])} THEN {lark_db.sql_str(row['result'])}"
            for row in chunk
        )
        lark_db.execute(
            "UPDATE commands SET "
            f"content = CASE id {content_cases} ELSE content END, "
            f"result = CASE id {result_cases} ELSE result END "
            f"WHERE id IN ({ids})"
        )
    return len(updates)


def redact_existing_rows() -> dict[str, int]:
    """Redact sensitive-looking text already persisted in Miaoda tables."""
    counts = {"sessions": 0, "events": 0, "commands": 0}

    sessions = lark_db.query(
        "SELECT id, cwd, summary FROM sessions WHERE "
        f"{_maybe_clause('cwd', include_personal=True)} "
        f"OR {_maybe_clause('summary', include_personal=True)}"
    )
    counts["sessions"] += _update_sessions(sessions)

    while True:
        events = lark_db.query(
            "SELECT id, content FROM events WHERE "
            + _maybe_clause("content")
            + " LIMIT 1000"
        )
        changed = _update_events(events)
        counts["events"] += changed
        if not events or changed == 0:
            break

    while True:
        commands = lark_db.query(
            "SELECT id, content, result FROM commands WHERE consumed = TRUE AND "
            f"({_maybe_clause('content')} OR {_maybe_clause('result')}) "
            "LIMIT 1000"
        )
        changed = _update_commands(commands)
        counts["commands"] += changed
        if not commands or changed == 0:
            break

    log.info("redacted rows: %s", counts)
    return counts
