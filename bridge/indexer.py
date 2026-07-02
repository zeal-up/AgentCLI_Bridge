"""Indexer: read local sessions from every agent adapter -> upsert into the
Miaoda `sessions` table (with an `agent` tag). Zero intrusion (read-only)."""
from __future__ import annotations
import datetime
import logging

from . import lark_db
from .agents import AGENTS

log = logging.getLogger(__name__)

COLS = "(id, agent, cwd, summary, updated_at, online, pid, indexed_at, ctx_used, ctx_limit)"
CHUNK = 50  # rows per upsert statement


def index() -> int:
    """Upsert all local sessions (all agents) into the Miaoda sessions table.
    Returns total count. display_name and hidden are NOT in COLS/SET, so user
    renames and archives survive indexer upserts. Stale-row cleanup is per-agent."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = 0
    ids_by_agent: dict[str, list[str]] = {}

    for adapter in AGENTS:
        key = adapter.key
        sessions = adapter.list_sessions()
        ids_by_agent[key] = [s["id"] for s in sessions]

        values: list[str] = []
        for s in sessions:
            sid = s["id"]
            pid = s.get("pid")
            # Context usage (best-effort; None -> NULL).
            ctx = adapter.get_context(sid)
            ctx_used = str(ctx["used"]) if ctx and ctx.get("used") is not None else "NULL"
            ctx_limit = str(ctx["limit"]) if ctx and ctx.get("limit") is not None else "NULL"
            values.append(
                "({id},{ag},{cwd},{sum},{upd},{onl},{pid},{idx},{cu},{cl})".format(
                    id=lark_db.sql_str(sid),
                    ag=lark_db.sql_str(key),
                    cwd=lark_db.sql_str(s.get("cwd")),
                    sum=lark_db.sql_str(s.get("summary")),
                    upd=lark_db.sql_str(s.get("updated_at")),
                    onl="TRUE" if s.get("online") else "FALSE",
                    pid=str(pid) if pid is not None else "NULL",
                    idx=lark_db.sql_str(now),
                    cu=ctx_used,
                    cl=ctx_limit,
                )
            )

        for i in range(0, len(values), CHUNK):
            chunk = values[i:i + CHUNK]
            sql = (
                f"INSERT INTO sessions {COLS} VALUES {','.join(chunk)} "
                "ON CONFLICT (id) DO UPDATE SET "
                "agent=EXCLUDED.agent, cwd=EXCLUDED.cwd, summary=EXCLUDED.summary, "
                "updated_at=EXCLUDED.updated_at, online=EXCLUDED.online, "
                "pid=EXCLUDED.pid, indexed_at=EXCLUDED.indexed_at, "
                "ctx_used=EXCLUDED.ctx_used, ctx_limit=EXCLUDED.ctx_limit"
            )
            lark_db.execute(sql)

        total += len(sessions)
        log.info("agent %s: %d sessions (%d online)", key, len(sessions),
                 sum(1 for s in sessions if s.get("online")))

    # Per-agent cleanup: remove rows of this agent no longer present locally.
    # (Scoped by agent so Copilot's cleanup never deletes Claude rows, etc.)
    for key, ids in ids_by_agent.items():
        if ids:
            in_list = ",".join(lark_db.sql_str(i) for i in ids)
            deleted = lark_db.execute(
                f"DELETE FROM sessions WHERE agent = {lark_db.sql_str(key)} "
                f"AND id NOT IN ({in_list})"
            )
        else:
            deleted = lark_db.execute(
                f"DELETE FROM sessions WHERE agent = {lark_db.sql_str(key)}"
            )
        if deleted:
            log.info("agent %s: cleaned up %d stale session rows", key, deleted)

    log.info("indexed %d sessions across %d agents", total, len(AGENTS))
    return total


if __name__ == "__main__":
    raise SystemExit(main()) if False else None
