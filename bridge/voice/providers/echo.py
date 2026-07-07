"""Echo backend (VOICE_ASR_BACKEND=echo) — the early-risk-gate provider.

Does NOT do ASR. After start(), each received PCM binary frame is reported
back as a partial whose text is the cumulative byte count, so the page can
confirm the full transport path (page WSS -> cloudflared -> bridge ->
page) works LIVE while speaking, with zero ASR deps. Not for production.
"""
from __future__ import annotations

from .base import ASRProvider


class EchoProvider(ASRProvider):
    name = "echo"

    def __init__(self, on_partial, on_final) -> None:
        super().__init__(on_partial, on_final)
        self._bytes = 0

    async def start(self, lang: str, sample_rate: int) -> None:
        self._bytes = 0
        await self._on_partial(f"[echo ready src={sample_rate} lang={lang}]")

    async def feed_pcm(self, pcm: bytes) -> None:
        self._bytes += len(pcm)
        # 640 bytes = 320 samples = 20ms @ 16k; report frames + ms.
        frames = self._bytes // 640
        ms = self._bytes // (16000 * 2 // 1000)
        await self._on_partial(f"[echo received {frames} frames ≈ {ms}ms]")

    async def stop(self) -> None:
        await self._on_final(f"[echo done {self._bytes} bytes]")
