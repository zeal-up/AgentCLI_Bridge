"""Agent CLI adapters.

Each adapter wraps a specific local agent CLI (Copilot, Claude Code, ...) and
exposes a uniform interface so the indexer / tailer / injector orchestrators
can be agent-agnostic.

Adapters are stateless singletons; per-session mutable state (e.g. the
toolCallId→toolName tracker) lives in a `ctx` dict created fresh per tail.
"""
from __future__ import annotations

from typing import Any

from .base import AgentAdapter
from .copilot import CopilotAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter

# Ordered registry. Indexer/tailer/injector loop over this.
AGENTS: list[AgentAdapter] = [CopilotAdapter(), ClaudeAdapter(), CodexAdapter()]

_BY_KEY: dict[str, AgentAdapter] = {a.key: a for a in AGENTS}


def get_adapter(agent: str | None) -> AgentAdapter:
    """Resolve an adapter by key; falls back to copilot for unknown/None."""
    if agent and agent in _BY_KEY:
        return _BY_KEY[agent]
    return _BY_KEY["copilot"]


def adapter_for_session(session_id: str) -> AgentAdapter | None:
    """Find which adapter owns a session id (by checking its events path exists)."""
    import os
    for a in AGENTS:
        try:
            p = a.events_path(session_id)
            if p and os.path.exists(p):
                return a
        except Exception:
            continue
    return None
