"""HMAC-signed voice relay tokens.

The Miaoda NestJS app (GET /api/voice/config) signs a short-lived token
embedding the Feishu user_id; the bridge relay verifies it and checks the
userId against config.ALLOWED_OPEN_IDS. Same VOICE_RELAY_SECRET on both
sides. Stdlib only — no deps.

Token format:  base64url(payload_json) "." base64url(hmac_sha256(payload, secret))
payload = {"uid": <userId>, "exp": <unix-ms>}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_token(user_id: str, secret: str, ttl: int = 300) -> str:
    """Sign a token for `user_id` valid for `ttl` seconds (default 5min)."""
    if not secret:
        raise ValueError("VOICE_RELAY_SECRET is empty; cannot sign voice token")
    exp_ms = int(time.time() * 1000) + int(ttl * 1000)
    payload = json.dumps({"uid": user_id, "exp": exp_ms}, separators=(",", ":"))
    payload_b = payload.encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), payload_b, hashlib.sha256).digest()
    return _b64url(payload_b) + "." + _b64url(sig)


def verify_token(token: str, secret: str) -> str | None:
    """Return the userId if the token is valid & unexpired, else None.

    Constant-time on the signature; exp checked against wall clock.
    """
    if not token or not secret or "." not in token:
        return None
    payload_b64, sig_b64 = token.split(".", 1)
    try:
        payload_b = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except Exception:
        return None
    expected = hmac.new(secret.encode("utf-8"), payload_b, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(payload_b)
    except Exception:
        return None
    exp_ms = payload.get("exp")
    uid = payload.get("uid")
    if not isinstance(exp_ms, (int, float)) or not isinstance(uid, str):
        return None
    if int(time.time() * 1000) >= exp_ms:
        return None
    return uid
