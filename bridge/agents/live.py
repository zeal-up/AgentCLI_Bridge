"""Shared live-session detection for agent CLIs that run in tmux.

An interactive agent process (claude/codex/...) is one whose cmdline basename
matches and whose fd 0 is a real tty (excludes headless `-p`/`exec` subprocesses
the bridge itself spawns). We map such processes by their cwd to the session
they're running, then walk the parent chain to find the owning tmux pane and
send-keys the message into the live terminal.
"""
from __future__ import annotations

import os
import subprocess
import time

_CACHE: dict[str, tuple[float, dict[str, int]]] = {}  # basename -> (ts, {realpath(cwd): pid})
_CACHE_TTL = 5.0


def _interactive_pids(basename: str) -> list[int]:
    """PIDs of interactive (tty-attached) processes whose executable basename
    matches. Excludes headless runs (fd 0 = /dev/null)."""
    out: list[int] = []
    try:
        pids = os.listdir('/proc')
    except OSError:
        return out
    for pid in pids:
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as fh:
                cmd = fh.read().decode('utf-8', 'replace')
        except OSError:
            continue
        if not cmd:
            continue
        base = cmd.split('\x00')[0].split('/')[-1]
        if base != basename:
            continue
        try:
            fd0 = os.readlink(f'/proc/{pid}/fd/0')
        except OSError:
            continue
        if fd0.startswith('/dev/pts/') or fd0 == '/dev/tty':
            out.append(int(pid))
    return out


def live_cwd_map(basename: str) -> dict[str, int]:
    """{realpath(cwd): pid} for interactive processes of this agent. Cached."""
    now = time.time()
    cached = _CACHE.get(basename)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]
    out: dict[str, int] = {}
    for pid in _interactive_pids(basename):
        try:
            cwd = os.readlink(f'/proc/{pid}/cwd')
            out[os.path.realpath(cwd)] = pid
        except OSError:
            continue
    _CACHE[basename] = (now, out)
    return out


def find_live_pid_for_cwd(basename: str, cwd: str | None) -> int | None:
    """The interactive agent pid whose cwd == `cwd`, or None."""
    if not cwd:
        return None
    try:
        key = os.path.realpath(cwd)
    except OSError:
        return None
    return live_cwd_map(basename).get(key)


def tmux_pane_for_pid(pid: int) -> str | None:
    """Find the tmux pane id whose process tree contains `pid`."""
    try:
        out = subprocess.run(
            ['tmux', 'list-panes', '-a', '-F', '#{pane_id} #{pane_pid}'],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return None
    if not out.strip():
        return None
    pane_pids: dict[int, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                pane_pids[int(parts[1])] = parts[0]
            except ValueError:
                continue
    cur = pid
    seen: set[int] = set()
    while cur and cur not in seen:
        seen.add(cur)
        if cur in pane_pids:
            return pane_pids[cur]
        try:
            with open(f'/proc/{cur}/stat') as fh:
                raw = fh.read()
            after = raw.split(')', 1)[1].split()
            ppid = int(after[1])
        except Exception:
            return None
        if ppid <= 1:
            return None
        cur = ppid
    return None


def tmux_send_text(
    pane: str,
    content: str,
    submit_key: str = "Enter",
    newline_key: str | None = None,
    verify: bool = False,
) -> bool:
    """Type `content` into a tmux pane and send the TUI submit key.

    When `newline_key` is given (e.g. codex's ``C-j``), multi-line content is
    sent line-by-line with that key inserted between lines. This matters for
    TUIs like codex where crossterm turns a raw LF byte (from ``send-keys -l``)
    into an Enter key event — which would *submit* the draft prematurely.
    Sending the TUI's own insert-newline key between lines avoids that. When
    `newline_key` is None, content is sent verbatim with ``-l`` (legacy
    behavior for copilot/claude).

    A settle pause is inserted before the submit key: TUI composers (notably
    codex's) need a beat to commit just-typed characters before they'll honor
    the submit key. Sending Enter immediately after ``send-keys -l`` arrives
    while the input is still composing, so the Enter is swallowed/inserted as
    a newline instead of submitting — the message sits in the composer unsent.

    When `verify` is True (codex), confirm submission succeeded by reading the
    pane back: codex shows ``Working (… • esc to interrupt)`` once the draft is
    submitted, and the draft text stays on the ``›`` composer line if it
    wasn't. Retry the submit key a few times if it didn't take."""
    try:
        if newline_key:
            # Drop trailing newlines so we don't fire an extra submit/blank line.
            body = content.rstrip("\r\n")
            lines = body.split("\n")
            for i, line in enumerate(lines):
                if i > 0:
                    subprocess.run(['tmux', 'send-keys', '-t', pane, newline_key],
                                    capture_output=True, text=True, timeout=10)
                if line:
                    subprocess.run(['tmux', 'send-keys', '-t', pane, '-l', line],
                                    capture_output=True, text=True, timeout=10)
        else:
            subprocess.run(['tmux', 'send-keys', '-t', pane, '-l', content],
                            capture_output=True, text=True, timeout=10)
            body = content
        first_line = (body.split("\n")[0] or "").strip()

        def _submitted() -> bool:
            """True if the pane shows codex's working indicator (submit took)."""
            try:
                shot = subprocess.run(
                    ['tmux', 'capture-pane', '-t', pane, '-p', '-S', '-10'],
                    capture_output=True, text=True, timeout=10,
                ).stdout
            except Exception:
                return True  # can't read pane — assume success, don't retry blindly
            if 'esc to interrupt' in shot or 'Working' in shot:
                return True
            # Draft still sitting on the `›` composer line => not submitted.
            for ln in shot.splitlines():
                s = ln.strip()
                if s.startswith('›') and first_line and first_line in s:
                    return False
            # Composer cleared but no explicit working signal — assume submitted
            # (e.g. trivial prompt that finished before we could read the pane).
            return True

        if verify:
            for _ in range(4):
                time.sleep(0.35)
                subprocess.run(['tmux', 'send-keys', '-t', pane, submit_key],
                                capture_output=True, text=True, timeout=10)
                time.sleep(0.6)
                if _submitted():
                    return True
            return True  # keys were sent; gave up verifying
        time.sleep(0.35)
        subprocess.run(['tmux', 'send-keys', '-t', pane, submit_key],
                        capture_output=True, text=True, timeout=10)
        return True
    except Exception:
        return False
