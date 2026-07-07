"""Bridge configuration. All sensitive/instance-specific values come from env
vars (see .env.example). No real secrets live in this file."""
import os

# Miaoda app (the Feishu-side full_stack app). Required.
APP_ID = os.environ.get("COPILOT_BRIDGE_APP_ID", "")
LARK_PROFILE = os.environ.get("COPILOT_BRIDGE_PROFILE", "default")
DB_ENV = os.environ.get("COPILOT_BRIDGE_DB_ENV", "online")  # online = what the released app reads

# Local Copilot state
COPILOT_HOME = os.path.expanduser(os.environ.get("COPILOT_HOME", "~/.copilot"))
SESSION_STORE_DB = os.path.join(COPILOT_HOME, "session-store.db")
SESSION_STATE_DIR = os.path.join(COPILOT_HOME, "session-state")

# Feishu users allowed to send commands. These are the app-scoped user_id
# values the NestJS backend sees via req.userContext.userId (NOT the ou_...
# open_id). Find yours via the commands table after a first (forbidden) send:
#   SELECT DISTINCT sender_open_id FROM commands WHERE result LIKE 'forbidden%';
ALLOWED_OPEN_IDS = {
    s for s in os.environ.get("COPILOT_BRIDGE_ALLOW_OPEN_IDS", "").split(",") if s
}

# Local bridge state (offsets, audit) — created lazily
BRIDGE_STATE_DB = os.environ.get(
    "COPILOT_BRIDGE_STATE_DB",
    os.path.expanduser("~/.copilot-bridge/bridge-state.db"),
)

# ---- Streaming voice input relay (optional, OFF by default) ----
# Voice is OFF unless VOICE_ASR_BACKEND is set to a real backend AND
# VOICE_RELAY_SECRET is non-empty. The relay runs as a 4th daemon
# (scripts/bridge-start.sh) only when the backend is set.
#   none      -> voice disabled (default; page falls back to Web Speech)
#   dashscope -> cloud paraformer-realtime-v2 via wss://dashscope.aliyuncs.com
#   funasr    -> local self-hosted FunASR streaming server (GPU)
#   echo      -> dev/risk-gate: echoes PCM byte-count back as partial text
VOICE_ASR_BACKEND = os.environ.get("COPILOT_BRIDGE_VOICE_ASR_BACKEND", "none")
DASHSCOPE_API_KEY = os.environ.get("COPILOT_BRIDGE_DASHSCOPE_API_KEY", "")
FUNASR_WSS_URL = os.environ.get("COPILOT_BRIDGE_FUNASR_WSS_URL", "ws://localhost:10095")
VOICE_RELAY_PORT = int(os.environ.get("COPILOT_BRIDGE_VOICE_RELAY_PORT", "8765"))
# Shared HMAC secret with the Miaoda NestJS app (VOICE_RELAY_SECRET there).
# The page gets a short-lived token from GET /api/voice/config; the relay
# verifies it and checks the embedded userId against ALLOWED_OPEN_IDS.
VOICE_RELAY_SECRET = os.environ.get("COPILOT_BRIDGE_VOICE_RELAY_SECRET", "")

def voice_enabled() -> bool:
    """True if the relay should accept connections at all."""
    return VOICE_ASR_BACKEND not in ("", "none") and bool(VOICE_RELAY_SECRET)
