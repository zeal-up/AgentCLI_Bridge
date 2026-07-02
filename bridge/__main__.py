"""Bridge CLI. Usage:
  python -m bridge index   # upsert local sessions into Miaoda sessions table
  python -m bridge ls      # show sessions currently in the Miaoda table
  python -m bridge lock    # show local live inuse locks (online sessions)
  python -m bridge tail --session <id> [--once]  # tail one session's events
  python -m bridge tail --all [--once]            # tail all sessions' events
  python -m bridge events --session <id> [--limit N]  # query Miaoda events table
  python -m bridge inject [--once]  # poll commands table and inject into sessions
"""
from __future__ import annotations
import json
import logging
import sys

from . import indexer, lark_db, config, tailer, injector
from .agents import AGENTS


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main(argv: list[str]) -> int:
    _setup_logging()
    cmd = argv[1] if len(argv) > 1 else "index"

    if cmd == "index":
        n = indexer.index()
        print(f"indexed {n} local sessions into Miaoda app {config.APP_ID}")
        return 0

    if cmd == "ls":
        rows = lark_db.query(
            "SELECT id, agent, cwd, summary, online, pid, updated_at, indexed_at "
            "FROM sessions ORDER BY updated_at DESC"
        )
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        print(f"({len(rows)} rows)")
        return 0

    if cmd == "lock":
        live = {a.key: [s["id"] for s in a.list_sessions() if s.get("online")] for a in AGENTS}
        print(json.dumps(live, indent=2, ensure_ascii=False))
        total = sum(len(v) for v in live.values())
        print(f"({total} live online sessions)")
        return 0

    if cmd == "tail":
        return _cmd_tail(argv[2:])

    if cmd == "events":
        return _cmd_events(argv[2:])

    if cmd == "inject":
        return _cmd_inject(argv[2:])

    print(f"unknown command: {cmd!r} (try: index, ls, lock, tail, events, inject)", file=sys.stderr)
    return 2


def _cmd_tail(args: list[str]) -> int:
    """Parse tail subcommand args and invoke tailer."""
    once = "--once" in args
    session_id = None
    all_sessions = False

    i = 0
    while i < len(args):
        if args[i] == "--session" and i + 1 < len(args):
            session_id = args[i + 1]
            i += 2
        elif args[i] == "--all":
            all_sessions = True
            i += 1
        elif args[i] == "--once":
            i += 1
        else:
            print(f"tail: unknown arg: {args[i]!r}", file=sys.stderr)
            return 2

    if session_id and all_sessions:
        print("tail: cannot specify both --session and --all", file=sys.stderr)
        return 2

    # Default: --all when --once, require explicit --all or --session for daemon mode
    if not session_id and not all_sessions:
        if once:
            all_sessions = True  # snapshot mode: process all sessions by default
            log = logging.getLogger(__name__)
            log.info("tail --once: defaulting to --all (snapshot all sessions)")
        else:
            print("tail: in daemon mode, specify --session <id> or --all", file=sys.stderr)
            print("  (without --all, daemon mode defaults are ambiguous; pick one)", file=sys.stderr)
            return 2

    if session_id:
        n = tailer.tail_session(session_id, once=once)
        print(f"tailed session {session_id}: {n} rows inserted")
    else:
        n = tailer.tail_all(once=once)
        print(f"tailed all sessions: {n} rows inserted")
    return 0


def _cmd_events(args: list[str]) -> int:
    """Query the Miaoda events table for a session and print as JSON."""
    session_id = None
    limit = 20

    i = 0
    while i < len(args):
        if args[i] == "--session" and i + 1 < len(args):
            session_id = args[i + 1]
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                print("events: --limit must be an integer", file=sys.stderr)
                return 2
            i += 2
        else:
            print(f"events: unknown arg: {args[i]!r}", file=sys.stderr)
            return 2

    if not session_id:
        print("events: --session <id> is required", file=sys.stderr)
        return 2

    sql = (
        f"SELECT id, session_id, role, content, ts FROM events "
        f"WHERE session_id = {lark_db.sql_str(session_id)} "
        f"ORDER BY ts LIMIT {limit}"
    )
    rows = lark_db.query(sql)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"({len(rows)} rows)")
    return 0


def _cmd_inject(args: list[str]) -> int:
    """Poll commands table and inject into sessions. --once for single pass."""
    once = "--once" in args
    if once:
        n = injector.poll_once()
        print(f"inject --once: processed {n} commands")
        return 0
    # Daemon mode: loop
    injector.poll_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
