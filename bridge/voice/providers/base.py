"""ASR provider interface. One backend per relay connection.

Subclasses own their backend WebSocket client connection. The relay calls
start() -> feed_pcm(bytes) repeatedly -> stop(), and the provider invokes
on_partial/on_final/on_error callbacks (async) to push text/errors back to
the page.

Adding a backend = new file + a branch in make_provider(). The base class
keeps the relay uniform so dashscope/funasr/echo/disabled all plug in.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

# Callback signatures: (text: str) -> coroutine
OnPartial = Callable[[str], Awaitable[None]]
OnFinal = Callable[[str], Awaitable[None]]
OnError = Callable[[str], Awaitable[None]]


class ASRProvider:
    """Base class. Subclasses implement start/feed_pcm/stop.

    On a fatal backend error (task-failed, lost connection), the reader loop
    should call `await self._on_error(msg)` and return (stop reading), and set
    self._dead=True so feed_pcm silently drops further audio. This surfaces
    the failure to the page instead of dying silently.
    """

    name = "base"

    def __init__(self, on_partial: OnPartial, on_final: OnFinal, on_error: OnError) -> None:
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error
        self._dead = False

    async def start(self, lang: str, sample_rate: int) -> None:
        raise NotImplementedError

    async def feed_pcm(self, pcm: bytes) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError


class ProviderError(Exception):
    """Raised by a provider to signal a fatal backend error during start()."""


def make_provider(backend: str, on_partial: OnPartial, on_final: OnFinal, on_error: OnError) -> ASRProvider:
    """Factory. backend is config.VOICE_ASR_BACKEND."""
    # Local imports so a missing optional dep only errors when its backend
    # is actually selected (keeps `none`/`echo` dep-free).
    if backend in ("", "none"):
        from .disabled import DisabledProvider
        return DisabledProvider(on_partial, on_final, on_error)
    if backend == "echo":
        from .echo import EchoProvider
        return EchoProvider(on_partial, on_final, on_error)
    if backend == "dashscope":
        from .dashscope import DashScopeProvider
        return DashScopeProvider(on_partial, on_final, on_error)
    if backend == "funasr":
        from .funasr import FunAsrProvider
        return FunAsrProvider(on_partial, on_final, on_error)
    raise ProviderError(f"unknown voice backend: {backend!r}")
