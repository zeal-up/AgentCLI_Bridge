"""DashScope paraformer-realtime-v2 streaming ASR provider (raw WebSocket).

Connects to wss://dashscope.aliyuncs.com/api-ws/v1/inference with a Bearer
key, sends the run-task event, forwards PCM binary, and parses result-
generated events into partial/final text. The API key stays server-side.

Protocol notes (confirm end-marker field with one live capture in Phase 1):
- Request run-task event with header.action="run-task", streaming="out".
- Send PCM as binary frames (16k 16-bit mono; the page already resamples).
- finish-task on stop.
- Response: header.event="result-generated", payload.output.sentence.text
  is the recognized text. A sentence is FINAL when its end_time is set
  (>= 0) or an explicit is_sentence_end/sentence_end flag is present; we
  check several possible field names to be robust against doc drift.

If raw-WS proves fiddly, the dashscope Python SDK
(dashscope.audio.asr.Recognition + RecognitionCallback) is the reference
implementation and can wrap this provider instead.
"""
from __future__ import annotations

import json
import logging
import uuid

import websockets

from ... import config
from .base import ASRProvider, ProviderError

log = logging.getLogger(__name__)

WSS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


class DashScopeProvider(ASRProvider):
    name = "dashscope"

    def __init__(self, on_partial, on_final) -> None:
        super().__init__(on_partial, on_final)
        self._ws = None
        self._task_id = ""
        self._reader_task = None
        self._closed = False

    async def start(self, lang: str, sample_rate: int) -> None:
        if not config.DASHSCOPE_API_KEY:
            raise ProviderError("DASHSCOPE_API_KEY not set")
        self._task_id = uuid.uuid4().hex
        # language_hints accepts BCP-47 like "zh-CN"; map common forms.
        lang_hint = lang or "zh-CN"
        run_task = {
            "header": {
                "action": "run-task",
                "task_id": self._task_id,
                "streaming": "out",
            },
            "payload": {
                "model": "paraformer-realtime-v2",
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "parameters": {
                    "format": "pcm",
                    "sample_rate": 16000,
                    "language_hints": [lang_hint],
                    # semantic_punctuation_enabled=true => continuous, no VAD.
                    "semantic_punctuation_enabled": True,
                },
            },
            "input": {},
        }
        try:
            self._ws = await websockets.connect(
                WSS_URL,
                additional_headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
                max_size=None,
            )
        except Exception as e:
            raise ProviderError(f"dashscope connect failed: {e}") from e
        await self._ws.send(json.dumps(run_task))
        # Reader pumps result events -> callbacks.
        import asyncio
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                log.debug("dashscope event: %s", msg)
                header = msg.get("header", {}) or {}
                if header.get("event") == "task-failed":
                    await self._on_partial("")  # clear partial
                    raise ProviderError(f"dashscope task-failed: {header}")
                if header.get("event") != "result-generated":
                    continue
                sentence = ((msg.get("payload") or {}).get("output") or {}).get("sentence") or {}
                text = sentence.get("text") or ""
                if not text:
                    continue
                is_final = self._is_sentence_final(sentence)
                if is_final:
                    await self._on_final(text)
                else:
                    await self._on_partial(text)
        except ProviderError:
            raise
        except Exception as e:
            if not self._closed:
                raise ProviderError(f"dashscope read error: {e}") from e

    @staticmethod
    def _is_sentence_final(sentence: dict) -> bool:
        # Robust to doc drift: check the known flag names. end_time set
        # (>= 0) is the documented signal; also accept explicit booleans.
        if sentence.get("is_sentence_end") is True:
            return True
        if sentence.get("sentence_end") is True:
            return True
        end_time = sentence.get("end_time")
        if isinstance(end_time, (int, float)) and end_time >= 0:
            return True
        return False

    async def feed_pcm(self, pcm: bytes) -> None:
        if self._ws is None:
            raise ProviderError("dashscope not started")
        await self._ws.send(pcm)

    async def stop(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                finish = {"header": {"action": "finish-task", "task_id": self._task_id}}
                await self._ws.send(json.dumps(finish))
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
