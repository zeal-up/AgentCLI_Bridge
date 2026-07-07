"""FunASR streaming paraformer provider (local, self-hosted on GPU).

Connects to a local FunASR websocket server (default ws://localhost:10095),
sends the 2pass config handshake, forwards PCM binary, and parses result
JSON into partial/final text. No cloud dependency.

Protocol shape (CONFIRM exact fields from the canonical reference client
FunASR/runtime/python/websocket/funasr_wss_client.py before Phase 2 testing
— pull it via: gh api repos/modelscope/FunASR/contents/runtime/python/websocket/funasr_wss_client.py
or clone the repo. The fields below match the widely-documented shape; if
the server rejects the handshake, diff against that client):

  -> text: {"mode":"2pass","chunk_size":[5,10,5],"chunk_interval":10,
            "wav_name":"voice","is_speaking":true,"wav_format":"pcm",
            "itn":true,"audio_fs":16000}
  -> binary: raw 16k 16-bit mono PCM frames
  -> text: {"is_speaking":false}           # on stop
  <- text: {"mode":"2pass-online","text":"...","wav_name":"...","..."}  # partial
  <- text: {"mode":"2pass-offline","text":"...","wav_name":"...","..."} # final

2pass-online = interim (may change); 2pass-offline = final for the segment.
"""
from __future__ import annotations

import json
import logging

import websockets

from ... import config
from .base import ASRProvider, ProviderError

log = logging.getLogger(__name__)


class FunAsrProvider(ASRProvider):
    name = "funasr"

    def __init__(self, on_partial, on_final) -> None:
        super().__init__(on_partial, on_final)
        self._ws = None
        self._reader_task = None
        self._closed = False

    async def start(self, lang: str, sample_rate: int) -> None:
        url = config.FUNASR_WSS_URL or "ws://localhost:10095"
        try:
            self._ws = await websockets.connect(url, max_size=None)
        except Exception as e:
            raise ProviderError(f"funasr connect failed ({url}): {e}") from e
        cfg = {
            "mode": "2pass",
            "chunk_size": [5, 10, 5],
            "chunk_interval": 10,
            "wav_name": "voice",
            "is_speaking": True,
            "wav_format": "pcm",
            "itn": True,
            "audio_fs": 16000,
        }
        await self._ws.send(json.dumps(cfg))
        import asyncio
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                log.debug("funasr event: %s", msg)
                text = msg.get("text") or ""
                if not text:
                    continue
                mode = msg.get("mode", "")
                if mode == "2pass-offline":
                    await self._on_final(text)
                else:
                    # 2pass-online (and any interim variant) = partial.
                    await self._on_partial(text)
        except Exception as e:
            if not self._closed:
                raise ProviderError(f"funasr read error: {e}") from e

    async def feed_pcm(self, pcm: bytes) -> None:
        if self._ws is None:
            raise ProviderError("funasr not started")
        await self._ws.send(pcm)

    async def stop(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"is_speaking": False}))
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
