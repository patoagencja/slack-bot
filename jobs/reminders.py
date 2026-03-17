"""
Reminders job — runs daily at 9:00, sends due reminders to their channels.
"""
import logging
import _ctx
from tools.reminders import get_due_reminders, mark_sent, cleanup_old_reminders

logger = logging.getLogger(__name__)


def send_due_reminders():
    """
    Daily 9:00 — find all reminders due today (or overdue), send them, mark as sent.
    Also triggers cleanup of old sent reminders (>90 days).
    """
    due = get_due_reminders()
    if not due:
        logger.info("Reminders: brak przypomnień na dziś.")
        return

    sent_count = 0
    for r in due:
        try:
            _ctx.app.client.chat_postMessage(
                channel=r["channel_id"],
                text=(
                    f"🔔 *Reminder* (zaplanowany na {r['remind_date']})\n\n"
                    f"{r['message']}"
                ),
            )
            mark_sent(r["id"])
            sent_count += 1
            logger.info("📨 Reminder id=%s wysłany → channel=%s", r["id"], r["channel_id"])
        except Exception as e:
            logger.error("Błąd wysyłki reminder id=%s: %s", r["id"], e)

    logger.info("✅ Remindery wysłane: %d/%d", sent_count, len(due))

    # Weekly cleanup (runs every day but only deletes when there's something to delete)
    cleanup_old_reminders(days=90)
