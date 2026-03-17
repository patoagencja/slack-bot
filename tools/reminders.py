"""
Sebol Reminders — persistent date-based reminders stored in SQLite.

Reminders survive bot restarts and are never lost.
Sent reminders are cleaned up automatically after 90 days.
"""
import os
import sqlite3
import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "reminders.db")


# ── init ──────────────────────────────────────────────────────────────────────

def init_reminders():
    """Create reminders table if it doesn't exist. Call once at startup."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                channel_id  TEXT NOT NULL,
                remind_date TEXT NOT NULL,
                message     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                sent_at     TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_remind_date ON reminders(remind_date)")
        conn.commit()
    logger.info("✅ Reminders DB initialized: %s", DB_PATH)


# ── write ─────────────────────────────────────────────────────────────────────

def save_reminder(user_id: str, channel_id: str, remind_date: str, message: str) -> int:
    """
    Save a new reminder. remind_date must be 'YYYY-MM-DD'.
    Returns the new reminder id.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO reminders(user_id, channel_id, remind_date, message, created_at) VALUES (?,?,?,?,?)",
            (user_id, channel_id, remind_date, message, datetime.now().isoformat()),
        )
        conn.commit()
        reminder_id = cur.lastrowid
    logger.info("📌 Reminder saved id=%s date=%s user=%s", reminder_id, remind_date, user_id)
    return reminder_id


# ── read ──────────────────────────────────────────────────────────────────────

def get_due_reminders(for_date: str = None) -> list[dict]:
    """
    Return all unsent reminders due on or before for_date (default: today).
    Each dict: {id, user_id, channel_id, remind_date, message}
    """
    check_date = for_date or date.today().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, user_id, channel_id, remind_date, message FROM reminders "
            "WHERE sent_at IS NULL AND remind_date <= ? ORDER BY remind_date ASC",
            (check_date,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_reminders(user_id: str) -> list[dict]:
    """Return all pending (unsent) reminders for a given user."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, channel_id, remind_date, message, created_at FROM reminders "
            "WHERE user_id = ? AND sent_at IS NULL ORDER BY remind_date ASC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── mark sent ─────────────────────────────────────────────────────────────────

def mark_sent(reminder_id: int):
    """Mark a reminder as sent (records timestamp)."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE reminders SET sent_at = ? WHERE id = ?",
            (datetime.now().isoformat(), reminder_id),
        )
        conn.commit()
    logger.info("✅ Reminder id=%s marked as sent", reminder_id)


# ── cleanup ───────────────────────────────────────────────────────────────────

def cleanup_old_reminders(days: int = 90):
    """Delete sent reminders older than `days` days. Run periodically."""
    cutoff = datetime.now()
    from datetime import timedelta
    cutoff_str = (cutoff - timedelta(days=days)).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        deleted = conn.execute(
            "DELETE FROM reminders WHERE sent_at IS NOT NULL AND sent_at < ?",
            (cutoff_str,),
        ).rowcount
        conn.commit()
    if deleted:
        logger.info("🧹 Cleaned up %d old reminders (>%d days)", deleted, days)
