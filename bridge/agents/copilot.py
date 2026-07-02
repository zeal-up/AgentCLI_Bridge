"""Copilot CLI adapter — extracts the original Copilot-specific logic.

Storage (v1.0.67):
  ~/.copilot/session-store.db        sqlite global index (id, cwd, summary, ...)
  ~/.copilot/session-state/<id>/events.jsonl   append-only event log
  ~/.copilot/session-state/<id>/inuse.<pid>.lock  online marker
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sqlite3
import subprocess
import time
from typing import Any

from .base import AgentAdapter, context_limit_for
from . import live
from .. import config
from .. import lark_db

log = logging.getLogger(__name__)

COPILOT_BIN = os.environ.get("COPILOT_BRIDGE_COPILOT_BIN", "/usr/local/bin/copilot")
RESUME_TIMEOUT = int(os.environ.get("COPILOT_BRIDGE_RESUME_TIMEOUT", "600"))

_SKIP_TYPES = frozenset({
    "session.start", "session.model_change", "session.shutdown",
    "session.resume", "session.info", "session.mode_changed",
    "session.permissions_changed", "session.context_changed",
    "session.compaction_start", "session.compaction_complete",
    "session.plan_changed", "assistant.turn_start", "assistant.turn_end",
    "hook.start", "hook.end", "skill.invoked", "subagent.started",
    "subagent.completed", "system.message",
})

MAX_CONTENT_LEN = 4000
TRUNCATE_HEAD = 3800


def _truncate(text: str) -> str:
    text = text.replace("\x00", "")
    if len(text) <= MAX_CONTENT_LEN:
        return text
    return text[:TRUNCATE_HEAD] + "\n…[truncated]"


def _short_args(args: Any, limit: int = 200) -> str:
    s = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
    if len(s) > limit:
        s = s[:limit] + "…"
    return s


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _find_live_copilot_pid(session_id: str) -> int | None:
    """Find the interactive `copilot --resume=<session_id>` process by matching
    the session id in its cmdline (precise — no cwd/pane-name ambiguity).
    Excludes headless `-p` subprocesses (fd 0 = /dev/null)."""
    for pid in live._interactive_pids("copilot"):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        if session_id in cmd:
            return pid
    return None


def _tmux_pane_exists(target: str) -> bool:
    try:
        p = subprocess.run(["tmux", "has-session", "-t", target],
                           capture_output=True, text=True, timeout=5)
        return p.returncode == 0
    except Exception:
        return False


def _live_locks() -> dict[str, int]:
    """{session_id: pid} for sessions whose inuse.<pid>.lock pid is alive."""
    base = config.SESSION_STATE_DIR
    if not os.path.isdir(base):
        return {}
    live_sids: dict[str, int] = {}
    for sid in os.listdir(base):
        sd = os.path.join(base, sid)
        if not os.path.isdir(sd):
            continue
        for fn in os.listdir(sd):
            if not (fn.startswith("inuse.") and fn.endswith(".lock")):
                continue
            try:
                pid = int(fn[len("inuse."):-len(".lock")])
            except ValueError:
                continue
            if _pid_alive(pid):
                live_sids[sid] = pid
                break
    return live_sids


class CopilotAdapter(AgentAdapter):
    key = "copilot"
    label = "Copilot"

    # ---- indexing ---------------------------------------------------------

    def list_sessions(self) -> list[dict[str, Any]]:
        db = config.SESSION_STORE_DB
        if not os.path.exists(db):
            log.warning("session-store.db not found at %s", db)
            return []
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT id, cwd, repository, branch, summary, updated_at FROM sessions"
            ).fetchall()
        finally:
            con.close()
        live = _live_locks()
        out: list[dict[str, Any]] = []
        for r in rows:
            sid = r["id"]
            # Skip stub sessions (no events.jsonl) — they show empty in the UI.
            if not os.path.exists(os.path.join(config.SESSION_STATE_DIR, sid, "events.jsonl")):
                continue
            out.append({
                "id": sid,
                "cwd": r["cwd"],
                "summary": r["summary"],
                "updated_at": r["updated_at"],
                "online": sid in live,
                "pid": live.get(sid),
            })
        return out

    def is_online(self, session_id: str) -> bool:
        return session_id in _live_locks()

    def get_cwd(self, session_id: str) -> str | None:
        db = config.SESSION_STORE_DB
        if os.path.exists(db):
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                row = con.execute("SELECT cwd FROM sessions WHERE id = ?", (session_id,)).fetchone()
                con.close()
                if row and row[0]:
                    return row[0]
            except Exception as exc:
                log.warning("failed to read cwd from session-store.db: %s", exc)
        return None

    # ---- tailing ----------------------------------------------------------

    def discover_tail_ids(self) -> list[str]:
        base = config.SESSION_STATE_DIR
        if not os.path.isdir(base):
            return []
        sids: list[str] = []
        for sid in os.listdir(base):
            sd = os.path.join(base, sid)
            if os.path.isdir(sd) and os.path.exists(os.path.join(sd, "events.jsonl")):
                sids.append(sid)
        return sorted(sids)

    def events_path(self, session_id: str) -> str:
        return os.path.join(config.SESSION_STATE_DIR, session_id, "events.jsonl")

    def new_ctx(self) -> dict[str, Any]:
        return {"tool_name_map": {}}  # toolCallId → toolName

    def map_event(self, event: dict[str, Any], ctx: dict[str, Any]) -> list[tuple[str, str]]:
        etype = event.get("type", "")
        if etype in _SKIP_TYPES:
            return []
        data = event.get("data") or {}
        tool_name_map = ctx["tool_name_map"]

        if etype == "user.message":
            content = data.get("content", "")
            return [("user", _truncate(content))] if content else []

        if etype == "assistant.message":
            content = data.get("content", "")
            if content:
                return [("assistant", _truncate(content))]
            tool_reqs = data.get("toolRequests")
            if tool_reqs and isinstance(tool_reqs, list) and len(tool_reqs) > 0:
                lines = []
                for req in tool_reqs:
                    name = req.get("name", "?")
                    args = req.get("arguments", "")
                    tcid = req.get("toolCallId") or req.get("id")
                    if tcid:
                        tool_name_map[tcid] = name
                    lines.append(f"🔧 {name}({_short_args(args)})")
                return [("assistant", _truncate("\n".join(lines)))]
            return []

        if etype == "tool.execution_start":
            name = data.get("toolName", "?")
            args = data.get("arguments", "")
            tcid = data.get("toolCallId")
            if tcid:
                tool_name_map[tcid] = name
            return [("tool", _truncate(f"🔧 {name} ▶ {_short_args(args, 200)}"))]

        if etype == "tool.execution_complete":
            success = data.get("success", False)
            icon = "✅" if success else "❌"
            name = data.get("toolName")
            if not name:
                tcid = data.get("toolCallId")
                name = tool_name_map.get(tcid, "?") if tcid else "?"
            result_obj = data.get("result") or {}
            result_text = result_obj.get("content", "") if isinstance(result_obj, dict) else str(result_obj)
            if not result_text:
                result_text = result_obj.get("detailedContent", "") if isinstance(result_obj, dict) else ""
            return [("tool", _truncate(f"🔧 {name} {icon} {_short_args(result_text, 500)}"))]

        if etype == "system.notification":
            content = data.get("content", "")
            return [("system", _truncate(content))] if content else []

        if etype == "session.error":
            return [("system", _truncate(f"⚠️ {data.get('message', '')}"))]

        if etype == "abort":
            return [("system", _truncate(f"⏹ aborted ({data.get('reason', '')})"))]

        if etype == "permission.requested":
            pr = data.get("permissionRequest") or {}
            text = pr.get("fullCommandText") or pr.get("kind", "")
            return [("system", _truncate(f"🔒 permission: {_short_args(text, 200)}"))]

        if etype == "permission.completed":
            res = data.get("result") or {}
            return [("system", _truncate(f"🔒 {res.get('kind', '')}"))]

        return []

    def event_ts(self, event: dict[str, Any]) -> str:
        return event.get("timestamp", "")

    def stable_key(self, session_id: str, event: dict[str, Any]) -> str:
        key_id = event.get("id") or f"ts:{event.get('timestamp', '')}"
        return f"{session_id}\x1f{key_id}"

    # ---- injection --------------------------------------------------------

    def resume_offline(self, session_id: str, content: str, cwd: str) -> str:
        cmd = [
            COPILOT_BIN, "-p", content, "--resume", session_id,
            "--output-format", "json", "--allow-all-tools",
        ]
        log.info("copilot offline resume: session=%s cwd=%s", session_id, cwd)
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=RESUME_TIMEOUT)
        except subprocess.TimeoutExpired:
            log.error("copilot resume timeout for session %s", session_id)
            return f"timeout: resume exceeded {RESUME_TIMEOUT}s"
        if p.returncode != 0:
            err = (p.stderr or "(no stderr)")[:500]
            log.error("copilot resume failed for %s: rc=%s %s", session_id, p.returncode, err)
            return f"error: rc={p.returncode} stderr={err}"

        line_count = 0
        last_type: str | None = None
        for line in p.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            line_count += 1
            last_type = ev.get("type", "")
        return f"completed: {line_count} events, last_type={last_type}"

    def inject_online(self, session_id: str, content: str) -> str:
        # Live route (auto-scan): find the interactive `copilot --resume=<sid>`
        # process by matching the session id in its cmdline, then type the
        # message into its tmux pane. Falls back to the legacy named-pane
        # (`copilot-<sid>`) approach, then headless resume.
        pid = _find_live_copilot_pid(session_id)
        if pid:
            pane = live.tmux_pane_for_pid(pid)
            if pane:
                if live.tmux_send_text(pane, content):
                    return f"sent to tmux pane {pane} (live copilot pid {pid})"
                log.warning("copilot %s: tmux send failed, degrading", session_id)
            else:
                log.info("copilot %s live (pid %s) but not in a tmux pane; headless resume",
                         session_id, pid)
        # Legacy: a pane explicitly named copilot-<sid>.
        target = f"copilot-{session_id}"
        if _tmux_pane_exists(target):
            try:
                subprocess.run(["tmux", "send-keys", "-t", target, "-l", content],
                                capture_output=True, text=True, timeout=10)
                subprocess.run(["tmux", "send-keys", "-t", target, "Enter"],
                                capture_output=True, text=True, timeout=10)
                return f"sent to pane {target}"
            except Exception as exc:
                log.error("copilot tmux inject failed: %s", exc)
                return f"error: tmux send-keys failed ({exc})"
        return self._fallback_offline(session_id, content)

    def _fallback_offline(self, session_id: str, content: str) -> str:
        cwd = self.get_cwd(session_id) or os.path.expanduser("~")
        return self.resume_offline(session_id, content, cwd)

    def set_title(self, session_id: str, name: str) -> bool:
        """Update the Copilot session-store.db summary (native name field).
        Copilot may overwrite it on a later turn, but that's native behavior."""
        db = config.SESSION_STORE_DB
        if not os.path.exists(db):
            return False
        try:
            con = sqlite3.connect(db)
            con.execute("UPDATE sessions SET summary = ? WHERE id = ?", (name, session_id))
            con.commit()
            con.close()
            return True
        except Exception as exc:
            log.warning("copilot set_title failed for %s: %s", session_id, exc)
            return False

    def get_context(self, session_id: str) -> dict[str, Any] | None:
        """Copilot doesn't expose per-turn context in the transcript. The best
        signal is the last `session.compaction_complete.preCompactionTokens` —
        the context size at the most recent compaction (a lower bound on the
        current context). The window limit isn't recorded, so limit=None."""
        path = self.events_path(session_id)
        if not path or not os.path.exists(path):
            return None
        try:
            out = subprocess.run(["grep", "-F", "preCompactionTokens", "--", path],
                                 capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return None
        lines = [l for l in out.splitlines() if l.strip()]
        if not lines:
            return None
        try:
            ev = json.loads(lines[-1])
        except json.JSONDecodeError:
            return None
        data = ev.get("data") or {}
        used = data.get("preCompactionTokens")
        if used is None:
            return None
        return {"used": used, "limit": None, "model": None}
