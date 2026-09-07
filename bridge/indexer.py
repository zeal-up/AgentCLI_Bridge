"""Indexer: read local sessions from every agent adapter -> upsert into the
Miaoda `sessions` table (with an `agent` tag). Zero intrusion (read-only)."""
from __future__ import annotations
import datetime
import logging

from . import config, lark_db
from .agents import AGENTS
from .redact import redact_text

log = logging.getLogger(__name__)

COLS = ("(id, agent, cwd, summary, updated_at, online, pid, indexed_at, "
        "ctx_used, ctx_limit, req_bytes, req_limit)")
CHUNK = 50  # rows per upsert statement


def index() -> int:
    """Upsert all local sessions (all agents) into the Miaoda sessions table.
    Returns total count. hidden is NOT in COLS/SET, so archives survive indexer
    upserts. display_name is only cleared when the native CLI title changes to a
    different value, allowing CLI-side renames to show up after a page rename."""
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
            # Request-body size estimate (claude only; other agents -> NULL).
            req_bytes = str(ctx["body_bytes"]) if ctx and ctx.get("body_bytes") is not None else "NULL"
            req_limit = str(config.CLAUDE_BODY_LIMIT) if req_bytes != "NULL" else "NULL"
            values.append(
                "({id},{ag},{cwd},{sum},{upd},{onl},{pid},{idx},{cu},{cl},{rb},{rl})".format(
                    id=lark_db.sql_str(sid),
                    ag=lark_db.sql_str(key),
                    cwd=lark_db.sql_str(redact_text(s.get("cwd"))),
                    sum=lark_db.sql_str(redact_text(s.get("summary"))),
                    upd=lark_db.sql_str(s.get("updated_at")),
                    onl="TRUE" if s.get("online") else "FALSE",
                    pid=str(pid) if pid is not None else "NULL",
                    idx=lark_db.sql_str(now),
                    cu=ctx_used,
                    cl=ctx_limit,
                    rb=req_bytes,
                    rl=req_limit,
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
                "ctx_used=EXCLUDED.ctx_used, ctx_limit=EXCLUDED.ctx_limit, "
                "req_bytes=EXCLUDED.req_bytes, req_limit=EXCLUDED.req_limit, "
                "display_name=CASE "
                "WHEN sessions.display_name IS NOT NULL "
                "AND EXCLUDED.summary IS DISTINCT FROM sessions.summary "
                "AND EXCLUDED.summary IS DISTINCT FROM sessions.display_name "
                "THEN NULL ELSE sessions.display_name END"
            )
            lark_db.execute(sql)

        # Auto-archive sessions that Claude Code compacted into a successor:
        # the transcript ends with a continued-in marker and the conversation
        # moves on under the successor id. Without this, the frozen old row
        # lingers in the list under the same title (looks like a broken/
        # half-loaded conversation). Only archive when the successor exists
        # locally, so we never hide a conversation with no visible successor.
        # hidden is not touched by the upsert above, so this survives reruns.
        local_ids = {s["id"] for s in sessions}
        compacted = [
            s["id"] for s in sessions
            if s.get("continued_into") and s["continued_into"] in local_ids
        ]
        if compacted:
            in_list = ",".join(lark_db.sql_str(i) for i in compacted)
            archived = lark_db.execute(
                f"UPDATE sessions SET hidden = TRUE "
                f"WHERE id IN ({in_list}) AND NOT hidden"
            )
            if archived:
                log.info("agent %s: archived %d compacted session(s) whose "
                         "conversation continued in a successor session",
                         key, archived)

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
