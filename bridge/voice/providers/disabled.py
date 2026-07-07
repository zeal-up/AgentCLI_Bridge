"""Disabled backend (VOICE_ASR_BACKEND=none). The relay refuses connections
uniformly; this keeps the relay code path the same whether voice is on or off."""
from __future__ import annotations

from .base import ASRProvider, ProviderError


class DisabledProvider(ASRProvider):
    name = "none"

    async def start(self, lang: str, sample_rate: int) -> None:
        raise ProviderError("voice disabled (VOICE_ASR_BACKEND=none)")

    async def feed_pcm(self, pcm: bytes) -> None:
        raise ProviderError("voice disabled")

    async def stop(self) -> None:
        return
