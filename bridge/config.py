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
