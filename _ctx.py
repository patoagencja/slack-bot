"""
Shared global state for all Sebol modules.
Never import from project modules here — no circular imports possible.
bot.py sets these at startup before any job module is invoked.
"""

app = None              # Slack Bolt App instance (slack_bolt.App)
claude = None           # Anthropic client (anthropic.Anthropic)
google_ads_client = None  # Google Ads API client

# Weekly check-in state: user_id → {"messages": [...], "done": bool, "name": str}
checkin_responses = {}

# Claude conversation history per user: user_id → [{"role": ..., "content": ...}]
conversation_history = {}

# Campaign drafts pending approval: campaign_id → {campaign_id, adset_id, ad_ids, params, account_id}
campaign_drafts = {}

# Campaign creation pending info: user_id → {params, files, channel, thread_ts, round}
# Używane gdy bot pyta o brakujące pola przed stworzeniem kampanii
campaign_pending = {}

# Threads where bot has participated: set of (channel, thread_ts) tuples
# Allows bot to respond in threads without explicit mention
bot_threads: set = set()

# /kampania wizard state: user_id → {step, answers, files, source_channel}
campaign_wizard: dict = {}

# /kampaniagoogle wizard state: user_id → {messages, source_channel, thread_ts}
google_campaign_wizard: dict = {}

# /kampaniameta wizard state: user_id → {messages, source_channel, thread_ts, mode, resolved_mode}
meta_campaign_wizard: dict = {}

# Voice transcription cache: (channel, msg_ts) → transcribed text
# Allows thread handlers to recover voice message content from history
voice_cache: dict = {}

# Muted budget alerts: "{platform}_{client}_{campaign}" → ISO expiry datetime str
muted_alerts: dict = {}

# Pending calendar invite confirmations: action_id → {user_id, title, start, end, location, channel, thread_ts}
calendar_pending: dict = {}


# ── Wizard state persistence ──────────────────────────────────────────────────
import json as _json
import os as _os

_WIZARD_STATE_FILE = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "data", "wizard_state.json"
)

_MUTED_ALERTS_FILE = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "data", "muted_alerts.json"
)


def save_muted_alerts():
    """Persist muted alerts to disk."""
    try:
        _os.makedirs(_os.path.dirname(_MUTED_ALERTS_FILE), exist_ok=True)
        with open(_MUTED_ALERTS_FILE, "w", encoding="utf-8") as f:
            _json.dump(muted_alerts, f, ensure_ascii=False)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("save_muted_alerts failed: %s", e)


def load_muted_alerts():
    """Restore muted alerts from disk after restart."""
    try:
        if not _os.path.exists(_MUTED_ALERTS_FILE):
            return
        with open(_MUTED_ALERTS_FILE, encoding="utf-8") as f:
            data = _json.load(f)
        muted_alerts.update(data)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("load_muted_alerts failed: %s", e)


def _wizard_to_json(wizard: dict) -> dict:
    """Serialize wizard state — skip binary files (not JSON-serializable)."""
    return {k: v for k, v in wizard.items() if k != "files"}


def save_wizard_state():
    """Persist all wizard states to disk so they survive restarts."""
    try:
        payload = {
            "meta":   {uid: _wizard_to_json(w) for uid, w in meta_campaign_wizard.items()},
            "google": {uid: _wizard_to_json(w) for uid, w in google_campaign_wizard.items()},
            "kampania": {uid: _wizard_to_json(w) for uid, w in campaign_wizard.items()},
        }
        _os.makedirs(_os.path.dirname(_WIZARD_STATE_FILE), exist_ok=True)
        with open(_WIZARD_STATE_FILE, "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("save_wizard_state failed: %s", e)


def load_wizard_state():
    """Restore wizard states from disk after restart."""
    try:
        if not _os.path.exists(_WIZARD_STATE_FILE):
            return
        with open(_WIZARD_STATE_FILE, encoding="utf-8") as f:
            payload = _json.load(f)
        for uid, w in payload.get("meta", {}).items():
            w.setdefault("files", [])
            meta_campaign_wizard[uid] = w
        for uid, w in payload.get("google", {}).items():
            w.setdefault("files", [])
            google_campaign_wizard[uid] = w
        for uid, w in payload.get("kampania", {}).items():
            w.setdefault("files", [])
            campaign_wizard[uid] = w
        restored = sum(len(payload.get(k, {})) for k in ("meta", "google", "kampania"))
        if restored:
            import logging
            logging.getLogger(__name__).info("Restored %d wizard session(s) from disk", restored)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("load_wizard_state failed: %s", e)
