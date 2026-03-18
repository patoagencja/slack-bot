"""
Sebol Reminders — backed by Slack chat.scheduleMessage.

Reminders are stored in Slack (not locally), so they survive bot restarts,
redeploys, and ephemeral filesystem wipes.
"""
import logging
import pytz
from datetime import datetime

logger = logging.getLogger(__name__)

_WARSAW = pytz.timezone("Europe/Warsaw")
_REMINDER_PREFIX = "🔔 *Reminder*\n\n"


def schedule_reminder(client, channel_id: str, remind_date: str, message: str) -> str:
    """
    Schedule a Slack message for 9:00 Warsaw time on remind_date (YYYY-MM-DD).
    Returns the scheduled_message_id from Slack.
    """
    dt = datetime.strptime(remind_date, "%Y-%m-%d").replace(hour=9, minute=0, second=0)
    post_at = int(_WARSAW.localize(dt).timestamp())

    result = client.chat_scheduleMessage(
        channel=channel_id,
        post_at=post_at,
        text=f"{_REMINDER_PREFIX}{message}",
    )
    msg_id = result["scheduled_message_id"]
    logger.info("📌 Reminder scheduled id=%s date=%s channel=%s", msg_id, remind_date, channel_id)
    return msg_id


def list_reminders(client, channel_id: str) -> list[dict]:
    """
    Return all pending scheduled reminders for channel_id.
    Each dict: {id, post_at_iso, message}
    """
    result = client.chat_scheduledMessages_list(channel=channel_id)
    out = []
    for m in result.get("scheduled_messages", []):
        text = m.get("text", "")
        if not text.startswith(_REMINDER_PREFIX):
            continue
        post_dt = datetime.fromtimestamp(m["post_at"], tz=_WARSAW)
        out.append({
            "id":          m["id"],
            "post_at_iso": post_dt.strftime("%Y-%m-%d %H:%M"),
            "message":     text[len(_REMINDER_PREFIX):],
        })
    out.sort(key=lambda x: x["post_at_iso"])
    return out


def delete_reminder(client, channel_id: str, scheduled_message_id: str) -> bool:
    """Cancel a scheduled reminder. Returns True on success."""
    try:
        client.chat_deleteScheduledMessage(
            channel=channel_id,
            scheduled_message_id=scheduled_message_id,
        )
        logger.info("🗑️ Reminder deleted id=%s", scheduled_message_id)
        return True
    except Exception as e:
        logger.error("Błąd usuwania reminder id=%s: %s", scheduled_message_id, e)
        return False


# ── backward-compat stubs (no-ops) ────────────────────────────────────────────

def init_reminders():
    """No-op — kept for import compatibility."""
    logger.info("Reminders: using Slack scheduleMessage backend (no local DB)")


def save_reminder(*args, **kwargs):
    raise RuntimeError("save_reminder() deprecated — use schedule_reminder(client, ...)")
