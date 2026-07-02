"""Claude Code CLI adapter.

Storage (v2.1.198):
  ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl   append-only transcript
  ~/.claude/projects/<encoded-cwd>/<session-uuid>/         subagents / tool-results

Each transcript line is one JSON event with `type`, `uuid`, `timestamp`,
and (for user/assistant) a `message` with `role` + `content`.

Resume is cwd-scoped: `claude --resume <id>` only finds the session when run
from the session's original working directory, so get_cwd() is mandatory.
Headless `claude -p '<prompt>' --resume <id>` appends to the SAME transcript
(no tmux needed); the tailer picks up the new events.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any

from .base import AgentAdapter, context_limit_for
from . import live

log = logging.getLogger(__name__)

CLAUDE_BIN = os.environ.get("COPILOT_BRIDGE_CLAUDE_BIN", "/usr/bin/claude")
CLAUDE_HOME = os.path.expanduser(os.environ.get("CLAUDE_HOME", "~/.claude"))
PROJECTS_DIR = os.path.join(CLAUDE_HOME, "projects")
RESUME_TIMEOUT = int(os.environ.get("COPILOT_BRIDGE_RESUME_TIMEOUT", "600"))

# Skip these transcript event types (not conversation content).
_SKIP_TYPES = frozenset({
    "mode", "permission-mode", "ai-title", "last-prompt", "attachment",
    "file-history-snapshot", "queue-operation", "custom-title", "agent-name",
})

MAX_CONTENT_LEN = 4000
TRUNCATE_HEAD = 3800
ONLINE_RECENT_SEC = 60  # a session is "online" if its transcript was appended within this


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


def _decode_cwd_from_dirname(enc: str) -> str:
    """Best-effort decode of the encoded project dir name back to a cwd.
    Claude encodes cwd as: leading '/' dropped, '/' -> '-', other chars kept.
    So '/a/b' -> '-a-b'. Reverse: '-' -> '/'. (Chars like '@' '.' are kept as-is
    in v2.1.198, unlike older versions that stripped them.)"""
    if not enc.startswith("-"):
        return enc
    return "/" + enc[1:].replace("-", "/")


def _last_titles(fpath: str) -> tuple[str | None, str | None]:
    """Grep the transcript for the last custom-title and ai-title values.
    Returns (custom_title, ai_title). grep is cheap even on large files."""
    import subprocess
    custom: str | None = None
    ai: str | None = None
    try:
        out = subprocess.run(
            ["grep", "-E", '"(customTitle|aiTitle)"', fpath],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return (None, None)
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "custom-title" and ev.get("customTitle"):
            custom = ev["customTitle"]
        elif ev.get("type") == "ai-title" and ev.get("aiTitle"):
            ai = ev["aiTitle"]
    return (custom, ai)


class ClaudeAdapter(AgentAdapter):
    key = "claude"
    label = "Claude"

    def __init__(self) -> None:
        # sid -> {path, cwd, title, mtime, size}; refreshed lazily.
        self._scan_cache: dict[str, dict[str, Any]] = {}
        self._scan_ts: float = 0.0

    # ---- scan -------------------------------------------------------------

    def _scan(self, force: bool = False) -> dict[str, dict[str, Any]]:
        """Walk ~/.claude/projects/*/*.jsonl and build a sid→meta map.
        Cached for 30s unless forced. Only re-parses files that changed mtime/size."""
        now = time.time()
        if not force and self._scan_cache and (now - self._scan_ts) < 30:
            return self._scan_cache

        out: dict[str, dict[str, Any]] = {}
        if not os.path.isdir(PROJECTS_DIR):
            self._scan_cache = out
            self._scan_ts = now
            return out

        for proj in os.listdir(PROJECTS_DIR):
            pdir = os.path.join(PROJECTS_DIR, proj)
            if not os.path.isdir(pdir):
                continue
            for fn in os.listdir(pdir):
                if not fn.endswith(".jsonl"):
                    continue
                fpath = os.path.join(pdir, fn)
                if not os.path.isfile(fpath):
                    continue
                sid = fn[:-len(".jsonl")]
                try:
                    st = os.stat(fpath)
                except OSError:
                    continue
                cached = self._scan_cache.get(sid)
                if (cached and cached.get("size") == st.st_size
                        and cached.get("mtime") == st.st_mtime):
                    out[sid] = cached
                    continue
                meta = self._parse_meta(fpath, proj)
                meta["path"] = fpath
                meta["mtime"] = st.st_mtime
                meta["size"] = st.st_size
                out[sid] = meta

        # drop sessions that disappeared
        self._scan_cache = out
        self._scan_ts = now
        return out

    def _parse_meta(self, fpath: str, proj_enc: str) -> dict[str, Any]:
        """Extract cwd + title. cwd from the head; title prefers the user's
        custom-title (manual rename), then ai-title, then first real user msg.
        Titles can appear anywhere in the file, so we grep for the last of each."""
        cwd: str | None = None
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
                    if not cwd and ev.get("cwd"):
                        cwd = ev["cwd"]
                    if (first_user is None and ev.get("type") == "user"
                            and isinstance(ev.get("message"), dict)):
                        c = ev["message"].get("content")
                        if (isinstance(c, str) and c.strip()
                                and not c.strip().startswith("<system-reminder>")):
                            first_user = c.strip()
        except OSError as exc:
            log.warning("failed to read %s: %s", fpath, exc)

        custom_title, ai_title = _last_titles(fpath)
        title = custom_title or ai_title or (first_user or "")[:80] or None

        if not cwd:
            cwd = _decode_cwd_from_dirname(proj_enc)
        return {"cwd": cwd, "title": title}

    # ---- injection / rename ------------------------------------------------

    def set_title(self, session_id: str, name: str) -> bool:
        """Append a custom-title event so the CLI picks up the new name on its
        next resume/open. Append-only (same model as the tailer reads)."""
        self._scan(force=True)  # fresh path lookup (cache may have evicted sid)
        path = self.events_path(session_id)
        if not path:
            return False
        import datetime as _dt
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        event = {
            "type": "custom-title",
            "customTitle": name,
            "sessionId": session_id,
            "timestamp": ts,
        }
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            return True
        except OSError as exc:
            log.error("claude set_title failed for %s: %s", session_id, exc)
            return False

    def get_context(self, session_id: str) -> dict[str, Any] | None:
        """Current context = latest assistant turn's input tokens (input +
        cache_creation + cache_read). Limit from the model map."""
        path = self.events_path(session_id)
        if not path or not os.path.exists(path):
            return None
        try:
            out = subprocess.run(["grep", "-F", '"usage"', "--", path],
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
        msg = ev.get("message") or {}
        u = msg.get("usage") or {}
        used = ((u.get("input_tokens") or 0)
                + (u.get("cache_creation_input_tokens") or 0)
                + (u.get("cache_read_input_tokens") or 0))
        model = msg.get("model")
        return {"used": used, "limit": context_limit_for(model), "model": model}

    # ---- indexing ---------------------------------------------------------

    def list_sessions(self) -> list[dict[str, Any]]:
        scan = self._scan()
        now = time.time()
        out: list[dict[str, Any]] = []
        for sid, m in scan.items():
            mtime = m.get("mtime", 0)
            online = (now - mtime) < ONLINE_RECENT_SEC
            out.append({
                "id": sid,
                "cwd": m.get("cwd"),
                "summary": m.get("title"),
                "updated_at": _mtime_iso(mtime),
                "online": online,
                "pid": None,
            })
        return out

    def is_online(self, session_id: str) -> bool:
        m = self._scan().get(session_id)
        if not m:
            return False
        # "Live" iff an interactive claude process is running this session
        # (matched by real cwd + newest .jsonl in the project dir). Falls back
        # to mtime recency only as a last resort.
        if self._find_live_pid(m):
            return True
        return (time.time() - m.get("mtime", 0)) < ONLINE_RECENT_SEC

    def _find_live_pid(self, m: dict[str, Any]) -> int | None:
        """The interactive claude pid running this session, or None.

        Primary match: a live proc whose cwd == the session's recorded cwd, AND
        this transcript is the newest .jsonl in its project dir (so we don't
        hijack another session in the same cwd).

        Fallback for renamed cwds: if the recorded cwd no longer exists (the
        dir was renamed while the session was live), find an "orphan" live claude
        proc — one whose cwd matches NO session's recorded cwd. A renamed-cwd
        session's proc now runs in the new (unrecorded) cwd, so it shows up as
        an orphan. Link them 1:1 (only when there's exactly one orphan)."""
        path = m.get("path", "")
        cwd = m.get("cwd")
        pid = _find_live_claude_pid(path, cwd)
        if pid:
            return pid
        # stale cwd (dir renamed/deleted) → orphan-proc fallback
        if cwd and not os.path.exists(cwd):
            return self._orphan_live_pid()
        return None

    def _orphan_live_pid(self) -> int | None:
        """A live claude proc whose cwd matches no session's recorded cwd (i.e.
        the dir was renamed). Only returns a pid when exactly one orphan exists
        (ambiguous otherwise)."""
        proc_cwds = live.live_cwd_map("claude")  # {realpath(cwd): pid}
        if not proc_cwds:
            return None
        recorded: set[str] = set()
        for s in self._scan().values():
            c = s.get("cwd")
            if c:
                try:
                    recorded.add(os.path.realpath(c))
                except OSError:
                    pass
        orphans = [pid for cwd, pid in proc_cwds.items() if cwd not in recorded]
        if len(orphans) == 1:
            log.info("claude: one orphan live proc (renamed cwd) -> %s", orphans[0])
            return orphans[0]
        return None

    def get_cwd(self, session_id: str) -> str | None:
        m = self._scan(force=True).get(session_id)
        return m.get("cwd") if m else None

    # ---- tailing ----------------------------------------------------------

    def discover_tail_ids(self) -> list[str]:
        return sorted(self._scan().keys())

    def events_path(self, session_id: str) -> str:
        m = self._scan().get(session_id)
        return m["path"] if m else ""

    def map_event(self, event: dict[str, Any], ctx: dict[str, Any]) -> list[tuple[str, str]]:
        etype = event.get("type", "")
        if etype in _SKIP_TYPES:
            return []
        msg = event.get("message")
        if not isinstance(msg, dict):
            return []

        # --- user ---
        if etype == "user":
            content = msg.get("content")
            if isinstance(content, str):
                txt = content.strip()
                # Skip Claude-injected <system-reminder> annotations
                # (e.g. "The user named this session ..."). Not real user input.
                if not txt or txt.startswith("<system-reminder>"):
                    return []
                return [("user", _truncate(txt))]
            if isinstance(content, list):
                # tool_result blocks
                lines = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        r = block.get("content", "")
                        if isinstance(r, list):
                            r = " ".join(b.get("text", "") for b in r if isinstance(b, dict))
                        lines.append(f"🔧 result: {_short_args(r, 300)}")
                return [("tool", _truncate("\n".join(lines)))] if lines else []
            return []

        # --- assistant ---
        if etype == "assistant":
            content = msg.get("content")
            if not isinstance(content, list):
                return []
            # One event may carry text + tool_use blocks; emit a row per block
            # (the frontend folds 'tool' rows into the process block, the last
            # text row surfaces as the final answer).
            rows: list[tuple[str, str]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text" and block.get("text"):
                    rows.append(("assistant", _truncate(block["text"])))
                elif bt == "tool_use":
                    name = block.get("name", "?")
                    rows.append(("tool", _truncate(f"🔧 {name}({_short_args(block.get('input', ''), 200)})")))
                # thinking: skip (internal reasoning, large)
            return rows

        if etype == "system":
            content = msg.get("content") or event.get("content")
            if isinstance(content, str) and content.strip():
                return [("system", _truncate(content))]
            return []

        return []

    def event_ts(self, event: dict[str, Any]) -> str:
        return event.get("timestamp", "")

    def stable_key(self, session_id: str, event: dict[str, Any]) -> str:
        # Claude events carry their own uuid; namespace with the agent key so
        # ids never collide with Copilot's (different hash input).
        uid = event.get("uuid") or f"ts:{event.get('timestamp', '')}"
        return f"claude\x1f{session_id}\x1f{uid}"

    # ---- injection --------------------------------------------------------

    def resume_offline(self, session_id: str, content: str, cwd: str) -> str:
        cmd = [
            CLAUDE_BIN, "-p", content,
            "--resume", session_id,
            "--dangerously-skip-permissions",
            "--output-format", "text",
        ]
        # IS_SANDBOX=1 lets root use --dangerously-skip-permissions (the root
        # guard exists to protect CI; here it's the user's own dev box and
        # injection is gated by the Feishu sender allowlist).
        env = dict(os.environ)
        env["IS_SANDBOX"] = "1"
        log.info("claude offline resume: session=%s cwd=%s", session_id, cwd)
        try:
            p = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True,
                timeout=RESUME_TIMEOUT, stdin=subprocess.DEVNULL, env=env,
            )
        except subprocess.TimeoutExpired:
            log.error("claude resume timeout for session %s", session_id)
            return f"timeout: resume exceeded {RESUME_TIMEOUT}s"
        if p.returncode != 0:
            err = (p.stderr or "(no stderr)")[:500]
            log.error("claude resume failed for %s: rc=%s %s", session_id, p.returncode, err)
            return f"error: rc={p.returncode} stderr={err}"
        out = (p.stdout or "").strip()
        return f"completed: {len(out)} chars of output"

    def inject_online(self, session_id: str, content: str) -> str:
        # Live route: find the interactive claude process running this session
        # and type the message into its tmux pane (true live sync — the running
        # CLI processes it, answer streams in the terminal AND appends to the
        # transcript for the tailer/page). Falls back to headless resume if no
        # live process / not in tmux.
        path = self.events_path(session_id)
        m = self._scan().get(session_id) or {}
        pid = self._find_live_pid(m)
        if pid:
            pane = live.tmux_pane_for_pid(pid)
            if pane:
                if live.tmux_send_text(pane, content):
                    return f"sent to tmux pane {pane} (live claude pid {pid})"
                log.warning("claude %s: tmux send failed, falling back to headless", session_id)
            else:
                log.info("claude %s live (pid %s) but not in a tmux pane; headless resume",
                         session_id, pid)
        cwd = self.get_cwd(session_id) or os.path.expanduser("~")
        return self.resume_offline(session_id, content, cwd)


def _mtime_iso(mtime: float) -> str:
    if not mtime:
        return ""
    import datetime as _dt
    return _dt.datetime.fromtimestamp(mtime, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Live-session detection (claude-specific disambiguation on top of live.py).
# Claude doesn't keep the transcript fd open or put the sessionId in env, so we
# match by cwd + the transcript being the newest .jsonl in its project dir.
# ---------------------------------------------------------------------------

def _newest_jsonl_in(sdir: str) -> str | None:
    """The most-recently-modified .jsonl in a project dir (the active one)."""
    try:
        files = [os.path.join(sdir, f) for f in os.listdir(sdir) if f.endswith('.jsonl')]
    except OSError:
        return None
    if not files:
        return None
    return max(files, key=lambda p: os.path.getmtime(p))


def _find_live_claude_pid(path: str, cwd: str | None) -> int | None:
    """The interactive claude pid running this session, or None.

    Match: an interactive claude process whose cwd == this session's cwd, AND
    this transcript is the newest .jsonl in its project dir (else another
    session in the same cwd is the active one and we must not hijack its pane).
    The real cwd comes from the transcript's own `cwd` field (the encoded dir
    name is lossy to decode)."""
    if not path or not cwd:
        return None
    sdir = os.path.dirname(path)
    newest = _newest_jsonl_in(sdir)
    if not newest:
        return None
    try:
        if os.path.realpath(path) != os.path.realpath(newest):
            return None
    except OSError:
        return None
    return live.find_live_pid_for_cwd('claude', cwd)


