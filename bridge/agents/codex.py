"""Codex CLI (codex-cli 0.116.0) adapter.

Storage:
  ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session-uuid>.jsonl
Each line: {"timestamp","type","payload"}. The first line is `session_meta`
with payload.id (session UUID) and payload.cwd. Conversation content lives in
`response_item` lines (payload.type = message/function_call/...).

Inject:
  headless: `codex exec resume <id> <prompt> --dangerously-bypass-approvals-and-sandbox
            --skip-git-repo-check` (appends to the same rollout file).
  live (tmux): send-keys into the running `codex resume` pane.
"""
from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import subprocess
import time
from typing import Any

from .base import AgentAdapter, context_limit_for
from . import live

log = logging.getLogger(__name__)

CODEX_BIN = os.environ.get("COPILOT_BRIDGE_CODEX_BIN", "codex")
CODEX_HOME = os.path.expanduser(os.environ.get("CODEX_HOME", "~/.codex"))
SESSIONS_DIR = os.path.join(CODEX_HOME, "sessions")
RESUME_TIMEOUT = int(os.environ.get("COPILOT_BRIDGE_RESUME_TIMEOUT", "600"))

MAX_CONTENT_LEN = 4000
TRUNCATE_HEAD = 3800
ONLINE_RECENT_SEC = 60


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


def _mtime_iso(mtime: float) -> str:
    if not mtime:
        return ""
    import datetime as _dt
    return _dt.datetime.fromtimestamp(mtime, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CodexAdapter(AgentAdapter):
    key = "codex"
    label = "Codex"

    def __init__(self) -> None:
        self._scan_cache: dict[str, dict[str, Any]] = {}
        self._cwd_newest: dict[str, str] = {}  # realpath(cwd) -> newest sid
        self._scan_ts: float = 0.0

    # ---- scan -------------------------------------------------------------

    def _scan(self, force: bool = False) -> dict[str, dict[str, Any]]:
        now = time.time()
        if not force and self._scan_cache and (now - self._scan_ts) < 30:
            return self._scan_cache

        out: dict[str, dict[str, Any]] = {}
        cwd_newest: dict[str, str] = {}
        files = glob.glob(os.path.join(SESSIONS_DIR, "*", "*", "*", "*.jsonl"))
        for fpath in files:
            try:
                st = os.stat(fpath)
            except OSError:
                continue
            # parse session_meta (first line) for id + cwd; cheap title from
            # the first real user message in the head.
            cached = self._scan_cache.get(_sid_from_path(fpath))
            sid = None
            meta = None
            # We need the sid to key the cache; read first line.
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                    first = fh.readline().strip()
            except OSError:
                continue
            try:
                ev = json.loads(first)
            except json.JSONDecodeError:
                continue
            payload = ev.get("payload") or {}
            sid = payload.get("id")
            if not sid:
                continue
            cached = self._scan_cache.get(sid)
            if (cached and cached.get("size") == st.st_size
                    and cached.get("mtime") == st.st_mtime):
                out[sid] = cached
            else:
                meta = self._parse_meta(fpath, payload)
                meta["path"] = fpath
                meta["mtime"] = st.st_mtime
                meta["size"] = st.st_size
                out[sid] = meta
            # track newest session per cwd (for live disambiguation)
            cwd = out[sid].get("cwd")
            if cwd:
                try:
                    key = os.path.realpath(cwd)
                except OSError:
                    continue
                cur = cwd_newest.get(key)
                if cur is None or out[sid]["mtime"] > out.get(cur, {}).get("mtime", 0):
                    cwd_newest[key] = sid

        self._scan_cache = out
        self._cwd_newest = cwd_newest
        self._scan_ts = now
        return out

    def _parse_meta(self, fpath: str, meta_payload: dict[str, Any]) -> dict[str, Any]:
        cwd = meta_payload.get("cwd")
        title: str | None = None
        first_user: str | None = None
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                for _ in range(2000):
                    line = fh.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") != "response_item":
                        continue
                    p = ev.get("payload") or {}
                    if not isinstance(p, dict):
                        continue
                    pt = p.get("type")
                    role = p.get("role")
                    if pt in ("message", "user_message", "agent_message") and role in ("user", None):
                        text = _first_text(p.get("content"))
                        if text and not text.startswith("<environment_context>") \
                                and not text.startswith("<permissions") \
                                and not text.startswith("<user_instructions"):
                            first_user = text.strip()
                            break
        except OSError as exc:
            log.warning("failed to read %s: %s", fpath, exc)
        title = (first_user or "")[:80] or None
        return {"cwd": cwd, "title": title}

    # ---- indexing ---------------------------------------------------------

    def list_sessions(self) -> list[dict[str, Any]]:
        scan = self._scan()
        now = time.time()
        out: list[dict[str, Any]] = []
        for sid, m in scan.items():
            mtime = m.get("mtime", 0)
            online = self._is_live(sid, m)
            out.append({
                "id": sid,
                "cwd": m.get("cwd"),
                "summary": m.get("title"),
                "updated_at": _mtime_iso(mtime),
                "online": online or (now - mtime) < ONLINE_RECENT_SEC,
                "pid": None,
            })
        return out

    def _is_live(self, session_id: str, m: dict[str, Any]) -> bool:
        """Live iff an interactive codex proc runs with cwd == this session's
        cwd AND this session is the newest one with that cwd (else another
        session in the same cwd is the active one)."""
        cwd = m.get("cwd")
        if not cwd:
            return False
        try:
            key = os.path.realpath(cwd)
        except OSError:
            return False
        if self._cwd_newest.get(key) != session_id:
            return False
        return live.find_live_pid_for_cwd("codex", cwd) is not None

    def is_online(self, session_id: str) -> bool:
        m = self._scan().get(session_id)
        if not m:
            return False
        if self._is_live(session_id, m):
            return True
        return (time.time() - m.get("mtime", 0)) < ONLINE_RECENT_SEC

    def get_cwd(self, session_id: str) -> str | None:
        m = self._scan(force=True).get(session_id)
        return m.get("cwd") if m else None

    def live_pane(self, session_id: str) -> str | None:
        m = self._scan().get(session_id) or {}
        if not self._is_live(session_id, m):
            return None
        pid = live.find_live_pid_for_cwd("codex", m.get("cwd"))
        return live.tmux_pane_for_pid(pid) if pid else None

    # ---- tailing ----------------------------------------------------------

    def discover_tail_ids(self) -> list[str]:
        return sorted(self._scan().keys())

    def events_path(self, session_id: str) -> str:
        m = self._scan().get(session_id)
        return m["path"] if m else ""

    def new_ctx(self) -> dict[str, Any]:
        return {}

    def map_event(self, event: dict[str, Any], ctx: dict[str, Any]) -> list[tuple[str, str]]:
        etype = event.get("type")
        if etype != "response_item":
            return []  # session_meta / event_msg / turn_context -> skip
        p = event.get("payload")
        if not isinstance(p, dict):
            return []
        pt = p.get("type")

        if pt == "message":
            role = p.get("role")
            text = _first_text(p.get("content"))
            if not text:
                return []
            if role == "user":
                # Skip codex-injected metadata wrappers.
                stripped = text.strip()
                if stripped.startswith("<environment_context>") \
                        or stripped.startswith("<permissions") \
                        or stripped.startswith("<user_instructions"):
                    return []
                return [("user", _truncate(text))]
            if role == "assistant":
                return [("assistant", _truncate(text))]
            return []  # developer / system -> skip

        if pt in ("user_message", "agent_message"):
            text = _first_text(p.get("content")) or str(p.get("content", ""))
            role = "user" if pt == "user_message" else "assistant"
            return [(role, _truncate(text))] if text else []

        if pt in ("function_call", "local_shell_call", "custom_tool_call"):
            name = p.get("name") or pt
            args = p.get("arguments") or p.get("action") or p.get("input") or ""
            return [("tool", _truncate(f"🔧 {name}({_short_args(args, 200)})"))]

        if pt in ("function_call_output", "local_shell_call_output", "custom_tool_call_output"):
            out = p.get("output")
            if isinstance(out, dict):
                out = out.get("content") or out
            return [("tool", _truncate(f"🔧 result: {_short_args(out, 300)}"))]

        # reasoning / token_count / others -> skip
        return []

    def event_ts(self, event: dict[str, Any]) -> str:
        return event.get("timestamp", "")

    def stable_key(self, session_id: str, event: dict[str, Any]) -> str:
        # Codex events have no stable id field; use timestamp + payload type +
        # role + a short payload hash to disambiguate same-timestamp events.
        p = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        phash = hashlib.blake2b(
            json.dumps(p, sort_keys=True, ensure_ascii=False).encode("utf-8"),
            digest_size=8,
        ).hexdigest()
        return f"codex\x1f{session_id}\x1f{event.get('timestamp', '')}\x1f{p.get('type', '')}\x1f{p.get('role', '')}\x1f{phash}"

    def is_turn_complete(self, event: dict[str, Any]) -> bool:
        # Codex has NO per-turn end event (turn_context only marks a turn
        # *start*). The only explicit "agent done" signals are task_complete
        # (agent declared the whole task finished) and turn_aborted. Mid-
        # conversation turns have no marker, so the page falls back to its
        # idle-settled heuristic for those.
        if event.get("type") != "event_msg":
            return False
        p = event.get("payload") or {}
        return p.get("type") in ("task_complete", "turn_aborted")

    # ---- injection --------------------------------------------------------

    def resume_offline(self, session_id: str, content: str, cwd: str) -> str:
        cmd = [
            CODEX_BIN, "exec", "resume", session_id, content,
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
        ]
        log.info("codex offline resume: session=%s cwd=%s", session_id, cwd)
        try:
            p = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True,
                timeout=RESUME_TIMEOUT, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            log.error("codex resume timeout for session %s", session_id)
            return f"timeout: resume exceeded {RESUME_TIMEOUT}s"
        if p.returncode != 0:
            err = (p.stderr or "(no stderr)")[:500]
            log.error("codex resume failed for %s: rc=%s %s", session_id, p.returncode, err)
            return f"error: rc={p.returncode} stderr={err}"
        out = (p.stdout or "").strip()
        return f"completed: {len(out)} chars of output"

    def inject_online(self, session_id: str, content: str) -> str:
        m = self._scan().get(session_id) or {}
        pid = live.find_live_pid_for_cwd("codex", m.get("cwd")) if self._is_live(session_id, m) else None
        if pid:
            pane = live.tmux_pane_for_pid(pid)
            if pane:
                if live.tmux_send_text(pane, content):
                    return f"sent to tmux pane {pane} (live codex pid {pid})"
                log.warning("codex %s: tmux send failed, falling back to headless", session_id)
            else:
                log.info("codex %s live (pid %s) but not in a tmux pane; headless resume",
                         session_id, pid)
        cwd = self.get_cwd(session_id) or os.path.expanduser("~")
        return self.resume_offline(session_id, content, cwd)

    def set_title(self, session_id: str, name: str) -> bool:
        # Codex thread names are not trivially writable from outside; page
        # renames persist via sessions.display_name (indexer preserves it).
        return False

    def get_context(self, session_id: str) -> dict[str, Any] | None:
        """used = latest token_count.info.last_token_usage.input_tokens (the
        full input context for the last turn; cached_input_tokens is a subset,
        NOT additive). limit = info.model_context_window (same event)."""
        path = self.events_path(session_id)
        if not path or not os.path.exists(path):
            return None
        info = None
        limit = None
        try:
            # token_count events with non-null info contain total_token_usage
            out = subprocess.run(["grep", "-F", "total_token_usage", "--", path],
                                 capture_output=True, text=True, timeout=10).stdout
            lines = [l for l in out.splitlines() if l.strip()]
            if lines:
                ev = json.loads(lines[-1])
                p = ev.get("payload") or {}
                info = p.get("info") or {}
        except Exception:
            pass
        used = None
        if info:
            last = info.get("last_token_usage") or {}
            used = last.get("input_tokens")
            limit = info.get("model_context_window") or limit
        # Fall back to task_started for the limit if not in token_count.
        if limit is None:
            try:
                out = subprocess.run(["grep", "-F", "model_context_window", "--", path],
                                     capture_output=True, text=True, timeout=10).stdout
                lines = [l for l in out.splitlines() if l.strip()]
                if lines:
                    ev = json.loads(lines[-1])
                    p = ev.get("payload") or {}
                    limit = p.get("model_context_window")
            except Exception:
                pass
        if used is None and limit is None:
            return None
        return {"used": used, "limit": limit, "model": None}


def _sid_from_path(fpath: str) -> str:
    """Best-effort sid from filename (fallback; the authoritative id is in
    session_meta, read separately)."""
    base = os.path.basename(fpath)
    # rollout-<ts>-<uuid>.jsonl
    parts = base.rsplit("-", 1)
    if len(parts) == 2 and parts[1].endswith(".jsonl"):
        return parts[1][:-len(".jsonl")]
    return base


def _first_text(content: Any) -> str:
    """Extract the first text block from a codex message content list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                t = block.get("text")
                if t:
                    return t
    return ""
