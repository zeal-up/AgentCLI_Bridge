"""Base interface for an agent CLI adapter."""
from __future__ import annotations

from typing import Any


# Best-effort context-window limits per model family (tokens). Codex provides
# its own limit via `model_context_window` in the transcript; for others we
# fall back to this map, then to 200000.
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "glm-5.2": 500000,
    "glm-5.1": 500000,
    "glm-5": 500000,
    "claude-sonnet-4.6": 200000,
    "claude-sonnet-4": 200000,
    "claude-opus-4": 200000,
    "claude-haiku-4": 200000,
    "gpt-5.3-codex": 258400,
    "qwen3.6-plus": 258400,
}

DEFAULT_CONTEXT_LIMIT = 200000


def context_limit_for(model: str | None) -> int | None:
    if not model:
        return None
    m = model.lower()
    for key, lim in MODEL_CONTEXT_LIMITS.items():
        if key in m:
            return lim
    return DEFAULT_CONTEXT_LIMIT


class AgentAdapter:
    """Uniform interface to a local agent CLI.

    Subclasses set `key` (DB tag) and `label` (display) and implement the
    session-discovery, event-mapping, and injection methods.
    """

    key: str = "agent"
    label: str = "Agent"

    # ---- indexing ---------------------------------------------------------

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return local sessions to upsert.

        Each dict: {id, cwd, summary, updated_at, online, pid}.
        """
        raise NotImplementedError

    def is_online(self, session_id: str) -> bool:
        """Whether a session is currently live (running CLI process)."""
        raise NotImplementedError

    def get_cwd(self, session_id: str) -> str | None:
        """Working directory for a session (for resume / display)."""
        raise NotImplementedError

    # ---- tailing ----------------------------------------------------------

    def discover_tail_ids(self) -> list[str]:
        """Session ids that have an event log to tail."""
        raise NotImplementedError

    def events_path(self, session_id: str) -> str:
        """Path to the append-only event log for a session."""
        raise NotImplementedError

    def new_ctx(self) -> dict[str, Any]:
        """Fresh per-session mutable state for map_event (e.g. tool trackers)."""
        return {}

    def map_event(self, event: dict[str, Any], ctx: dict[str, Any]) -> list[tuple[str, str]]:
        """Map a parsed JSONL event to a list of (role, content) rows.
        Empty list = skip this event. A single event may yield multiple rows
        (e.g. a Claude assistant message with both text and tool_use blocks)."""
        raise NotImplementedError

    def event_ts(self, event: dict[str, Any]) -> str:
        """ISO timestamp string for an event (used as the events.ts cursor)."""
        raise NotImplementedError

    def stable_key(self, session_id: str, event: dict[str, Any]) -> str:
        """Stable string key for dedup id generation. Must be invariant across
        re-runs (use the event's own id/uuid, not a byte offset)."""
        raise NotImplementedError

    def is_turn_complete(self, event: dict[str, Any]) -> bool:
        """True iff this event marks the end of an agent turn (the agent has
        finished responding and is idle, waiting for the next user input).
        The tailer emits a '✓ turn complete' marker on a True return so the
        page's send-lock can release precisely instead of guessing from
        transcript content. Default False (no reliable signal)."""
        return False

    # ---- injection --------------------------------------------------------

    def resume_offline(self, session_id: str, content: str, cwd: str) -> str:
        """Headless resume: run the CLI to append a turn to an idle session.
        Returns a result summary string."""
        raise NotImplementedError

    def inject_online(self, session_id: str, content: str) -> str:
        """Inject into a live (running) session, e.g. via tmux send-keys.
        Returns a result summary string."""
        raise NotImplementedError

    # ---- rename -----------------------------------------------------------

    def set_title(self, session_id: str, name: str) -> bool:
        """Write a user-chosen name back to the CLI's native session storage
        (e.g. Claude custom-title, Copilot session-store summary), so a page
        rename corresponds to the CLI. Return True on success."""
        return False

    # ---- context usage ----------------------------------------------------

    def get_context(self, session_id: str) -> dict[str, Any] | None:
        """Return {used, limit, model} for the session's current context usage,
        or None if unavailable. `used`/`limit` are token counts."""
        return None

    def live_pane(self, session_id: str) -> str | None:
        """The tmux pane id of the live interactive process running this session,
        or None (for terminal-capture / prompt-surfacing)."""
        return None
