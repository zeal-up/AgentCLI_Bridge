"""ASR provider interface. One backend per relay connection.

Subclasses own their backend WebSocket client connection. The relay calls
start() -> feed_pcm(bytes) repeatedly -> stop(), and the provider invokes
on_partial/on_final callbacks (async) to push text back to the page.

Adding a backend = new file + a branch in make_provider(). The base class
keeps the relay uniform so dashscope/funasr/echo/disabled all plug in.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

# Callback signatures: (text: str) -> coroutine
OnPartial = Callable[[str], Awaitable[None]]
OnFinal = Callable[[str], Awaitable[None]]


class ASRProvider:
    """Base class. Subclasses implement start/feed_pcm/stop."""

    name = "base"

    def __init__(self, on_partial: OnPartial, on_final: OnFinal) -> None:
        self._on_partial = on_partial
        self._on_final = on_final

    async def start(self, lang: str, sample_rate: int) -> None:
        raise NotImplementedError

    async def feed_pcm(self, pcm: bytes) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError


class ProviderError(Exception):
    """Raised by a provider to signal a fatal backend error to the relay."""


def make_provider(backend: str, on_partial: OnPartial, on_final: OnFinal) -> ASRProvider:
    """Factory. backend is config.VOICE_ASR_BACKEND."""
    # Local imports so a missing optional dep only errors when its backend
    # is actually selected (keeps `none`/`echo` dep-free).
    if backend in ("", "none"):
        from .disabled import DisabledProvider
        return DisabledProvider(on_partial, on_final)
    if backend == "echo":
        from .echo import EchoProvider
        return EchoProvider(on_partial, on_final)
    if backend == "dashscope":
        from .dashscope import DashScopeProvider
        return DashScopeProvider(on_partial, on_final)
    if backend == "funasr":
        from .funasr import FunAsrProvider
        return FunAsrProvider(on_partial, on_final)
    raise ProviderError(f"unknown voice backend: {backend!r}")
