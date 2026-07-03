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


def tmux_send_text(pane: str, content: str, submit_key: str = "Enter") -> bool:
    """Type `content` into a tmux pane and send the TUI submit key."""
    try:
        subprocess.run(['tmux', 'send-keys', '-t', pane, '-l', content],
                        capture_output=True, text=True, timeout=10)
        subprocess.run(['tmux', 'send-keys', '-t', pane, submit_key],
                        capture_output=True, text=True, timeout=10)
        return True
    except Exception:
        return False
