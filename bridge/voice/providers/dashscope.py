"""DashScope paraformer-realtime-v2 streaming ASR provider.

Wraps the official dashscope Python SDK (dashscope.audio.asr.Recognition)
rather than speaking the raw WS protocol — the SDK handles run-task /
continue-task / finish-task, auth, heartbeat, and protocol drift, and is
the canonical implementation. The SDK is synchronous (runs its WS on a
worker thread); this provider bridges its thread-callbacks onto the relay's
async event loop via asyncio.run_coroutine_threadsafe.

Dep: `pip install dashscope` (only needed when VOICE_ASR_BACKEND=dashscope;
echo/funasr/none don't require it). The API key stays server-side.
"""
from __future__ import annotations

import asyncio
import logging

from ... import config
from .base import ASRProvider, ProviderError

log = logging.getLogger(__name__)


class DashScopeProvider(ASRProvider):
    name = "dashscope"

    def __init__(self, on_partial, on_final, on_error) -> None:
        super().__init__(on_partial, on_final, on_error)
        self._rec = None
        self._loop = None
        self._first_result = False

    async def start(self, lang: str, sample_rate: int) -> None:
        if not config.DASHSCOPE_API_KEY:
            raise ProviderError("DASHSCOPE_API_KEY not set")
        import dashscope
        from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

        dashscope.api_key = config.DASHSCOPE_API_KEY
        self._loop = asyncio.get_running_loop()
        provider = self  # captured for the closure below

        class _CB(RecognitionCallback):
            def on_open(self) -> None:
                log.info("dashscope: on_open (connected)")

            def on_event(self, result) -> None:
                try:
                    sentence = result.get_sentence()
                    if not sentence:
                        return
                    text = (sentence.get("text") or "").strip()
                    if not text:
                        return
                    is_final = RecognitionResult.is_sentence_end(sentence)
                    if not provider._first_result:
                        provider._first_result = True
                        log.info(
                            "dashscope first result: is_final=%s text=%r",
                            is_final,
                            text[:60],
                        )
                    coro = (
                        provider._on_final(text) if is_final else provider._on_partial(text)
                    )
                    if provider._loop and not provider._loop.is_closed():
                        asyncio.run_coroutine_threadsafe(coro, provider._loop)
                except Exception as e:
                    log.warning("dashscope on_event error: %s", e)

            def on_error(self, result) -> None:
                msg = getattr(result, "message", None) or str(result)
                log.warning("dashscope on_error: %s", msg)
                try:
                    if provider._loop and not provider._loop.is_closed() and not provider._dead:
                        provider._dead = True
                        asyncio.run_coroutine_threadsafe(
                            provider._on_error(f"dashscope: {msg}"), provider._loop
                        )
                except Exception:
                    pass

            def on_close(self) -> None:
                log.info("dashscope: on_close")

        self._rec = Recognition(
            model="paraformer-realtime-v2",
            callback=_CB(),
            format="pcm",
            sample_rate=16000,
        )
        # start() launches the SDK worker thread (does the WS connect + sends
        # run-task). It may block briefly on connect, so run it in an executor
        # to keep the relay loop responsive.
        try:
            await self._loop.run_in_executor(None, self._rec.start)
        except Exception as e:
            raise ProviderError(f"dashscope start failed: {e}") from e

    async def feed_pcm(self, pcm: bytes) -> None:
        if self._dead or self._rec is None:
            return
        try:
            # send_audio_frame is sync (puts bytes on an internal queue); the
            # SDK worker thread drains it onto the WS. Safe to call from async.
            self._rec.send_audio_frame(pcm)
        except Exception as e:
            log.debug("dashscope feed_pcm dropped: %s", e)
            self._dead = True

    async def stop(self) -> None:
        if self._rec is None:
            return
        self._closed = True
        try:
            # stop() blocks: drains the queue, sends finish-task, waits for the
            # final sentence + worker join. Run in executor so the relay loop
            # stays free to dispatch the on_final callback that fires during
            # stop() (so the final text reaches the page before {ended}).
            await asyncio.get_running_loop().run_in_executor(None, self._rec.stop)
        except Exception as e:
            log.warning("dashscope stop error: %s", e)
