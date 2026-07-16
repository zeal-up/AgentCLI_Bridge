"""Bridge voice relay — async WebSocket server.

Page (Feishu WebView) -> cloudflared tunnel -> this server. Per connection:
verify the HMAC token, check the embedded userId against the allowlist,
then pump PCM binary frames to a per-connection ASR provider and stream
partial/final text frames back to the page.

First async component + first third-party dep (websockets) in the bridge.
Stdlib elsewhere (token.py uses hmac/hashlib/base64/json).

Page<->bridge protocol:
  in  (text)  {type:"auth", token}            # required first frame
  in  (text)  {type:"start", sampleRate, lang}
  in  (bin)   PCM 16k 16-bit mono
  in  (text)  {type:"stop"}
  out (text)  {type:"authed", backend} | {type:"error", message}
  out (text)  {type:"partial", text} | {type:"final", text}
  out (text)  {type:"ended"} | {type:"error", message}
"""
from __future__ import annotations

import asyncio
import json
import logging

import websockets

from .. import config
from .providers import base as provider_base
from .providers.base import ProviderError
from .token import verify_token

log = logging.getLogger(__name__)


def _msg(type_: str, **fields) -> str:
    return json.dumps({"type": type_, **fields}, ensure_ascii=False)


async def _handle_connection(ws) -> None:
    peer = ws.remote_address
    log.info("voice relay: connection from %s", peer)

    if not config.voice_enabled():
        # Voice off — tell the page immediately so it falls back to Web Speech.
        await ws.send(_msg("error", message="voice disabled on bridge"))
        await ws.close()
        return

    # 1. Auth (first text frame).
    try:
        first = await asyncio.wait_for(ws.recv(), timeout=10)
    except (asyncio.TimeoutError, websockets.ConnectionClosed):
        return
    try:
        auth = json.loads(first)
        token = auth.get("token") if auth.get("type") == "auth" else None
    except Exception:
        token = None
    user_id = verify_token(token or "", config.VOICE_RELAY_SECRET) if token else None
    if not user_id or user_id not in config.ALLOWED_OPEN_IDS:
        log.warning("voice relay: reject auth from %s (uid=%s)", peer, user_id)
        await ws.send(_msg("error", message="auth failed"))
        await ws.close()
        return
    log.info("voice relay: authed uid=%s backend=%s", user_id, config.VOICE_ASR_BACKEND)
    await ws.send(_msg("authed", backend=config.VOICE_ASR_BACKEND))

    # Result callbacks (shared across utterances on this connection).
    async def on_partial(text: str) -> None:
        try:
            await ws.send(_msg("partial", text=text))
        except websockets.ConnectionClosed:
            pass

    async def on_final(text: str) -> None:
        try:
            await ws.send(_msg("final", text=text))
        except websockets.ConnectionClosed:
            pass

    async def on_error(message: str) -> None:
        # Backend failed mid-utterance. Tell the page (it can retry with a new
        # {start}); do NOT close the connection — the WSS stays warm so the
        # next press-hold reuses it (preconnect optimization).
        log.warning("voice relay: provider error uid=%s: %s", user_id, message)
        try:
            await ws.send(_msg("error", message=message))
        except websockets.ConnectionClosed:
            pass

    # Multi-cycle session loop: the page preconnects on voice-mode enter
    # (auth only), then each press-hold sends {start} ... PCM ... {stop}.
    # One WSS connection serves many utterances so press-hold only pays the
    # ASR-connect cost, not the WSS+auth+mic setup.
    provider: provider_base.ASRProvider | None = None
    lang, sample_rate = "zh-CN", 16000
    try:
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                # PCM frame; route to the current utterance's provider (if any).
                if provider is not None:
                    await provider.feed_pcm(bytes(msg))
                continue
            try:
                ctrl = json.loads(msg)
            except Exception:
                continue
            t = ctrl.get("type")
            if t == "start":
                lang = ctrl.get("lang") or lang
                sample_rate = int(ctrl.get("sampleRate") or sample_rate)
                # Stop any previous utterance's provider before starting a new one.
                if provider is not None:
                    try:
                        await provider.stop()
                    except Exception as e:
                        log.warning("voice relay: prev provider stop: %s", e)
                    provider = None
                try:
                    provider = provider_base.make_provider(
                        config.VOICE_ASR_BACKEND, on_partial, on_final, on_error
                    )
                    await provider.start(lang, sample_rate)
                except ProviderError as e:
                    provider = None
                    await ws.send(_msg("error", message=str(e)))
                else:
                    # Ack so the page knows the ASR backend is ready before it
                    # streams (avoids dropping the first syllable).
                    await ws.send(_msg("started", backend=config.VOICE_ASR_BACKEND))
            elif t == "stop":
                if provider is not None:
                    try:
                        await provider.stop()
                    except Exception as e:
                        log.warning("voice relay: provider stop: %s", e)
                    provider = None
                try:
                    await ws.send(_msg("ended"))
                except websockets.ConnectionClosed:
                    pass
            # other control types ignored
    except websockets.ConnectionClosed:
        pass
    finally:
        if provider is not None:
            try:
                await provider.stop()
            except Exception as e:
                log.warning("voice relay: final provider stop: %s", e)
        try:
            await ws.close()
        except Exception:
            pass
        log.info("voice relay: connection closed uid=%s", user_id)


async def serve(port: int) -> None:
    """Run the voice relay server until interrupted."""
    if not config.voice_enabled():
        log.error(
            "voice relay: refusing to serve — VOICE_ASR_BACKEND=%r or "
            "VOICE_RELAY_SECRET empty. Set COPILOT_BRIDGE_VOICE_ASR_BACKEND "
            "(echo/dashscope/funasr) and COPILOT_BRIDGE_VOICE_RELAY_SECRET.",
            config.VOICE_ASR_BACKEND,
        )
        # Exit non-zero so bridge-start.sh's restart loop doesn't spin forever.
        raise SystemExit(2)

    log.info(
        "voice relay: listening on ws://0.0.0.0:%d backend=%s",
        port,
        config.VOICE_ASR_BACKEND,
    )
    # websockets 15: serve() is the top-level entry. max_size None = no cap on
    # PCM frame size (we send ~640 bytes, but be permissive).
    async with websockets.serve(_handle_connection, "0.0.0.0", port, max_size=None):
        await asyncio.Future()  # run forever
