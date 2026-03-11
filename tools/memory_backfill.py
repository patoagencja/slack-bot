"""
Backfill memory DB with historical Slack DM conversations.
Run once after deploying memory feature to populate history from the past.

Usage:
    python -m tools.memory_backfill
    python -m tools.memory_backfill --days 90   # limit to last 90 days
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_backfill(days: int = 365):
    from slack_bolt import App
    from tools.memory import init_memory, remember, DB_PATH
    import sqlite3

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        logger.error("SLACK_BOT_TOKEN not set")
        sys.exit(1)

    app = App(token=token)
    init_memory()

    oldest_ts = str((datetime.now() - timedelta(days=days)).timestamp())

    # ── 1. Find bot's own user ID ─────────────────────────────────────────────
    auth = app.client.auth_test()
    bot_user_id = auth["user_id"]
    logger.info("Bot user ID: %s", bot_user_id)

    # ── 2. List all open DM channels (im) ────────────────────────────────────
    logger.info("Fetching DM channel list...")
    dm_channels = []
    cursor = None
    while True:
        resp = app.client.conversations_list(
            types="im", limit=200, **({"cursor": cursor} if cursor else {})
        )
        dm_channels.extend(resp.get("channels", []))
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.3)

    logger.info("Found %d DM channels", len(dm_channels))

    # ── 3. For each DM, fetch full history and store ──────────────────────────
    total_stored = 0
    for ch in dm_channels:
        channel_id = ch["id"]
        other_user = ch.get("user", "unknown")  # the human on the other end

        if other_user == bot_user_id:
            continue  # skip slackbot DM

        logger.info("Processing DM channel %s (user %s)...", channel_id, other_user)
        page_cursor = None
        channel_count = 0

        while True:
            try:
                resp = app.client.conversations_history(
                    channel=channel_id,
                    oldest=oldest_ts,
                    limit=200,
                    **({"cursor": page_cursor} if page_cursor else {}),
                )
            except Exception as e:
                logger.warning("  Skipping channel %s: %s", channel_id, e)
                break

            messages = resp.get("messages", [])
            # API returns newest first — reverse for chronological storage
            for msg in reversed(messages):
                subtype = msg.get("subtype", "")
                if subtype in ("message_changed", "message_deleted", "bot_message"):
                    continue
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                ts = msg.get("ts", "")
                is_bot = bool(msg.get("bot_id")) or msg.get("user") == bot_user_id
                role = "assistant" if is_bot else "user"
                user_id = other_user  # key all messages by the human user_id

                remember(user_id, channel_id, ts, role, text)
                channel_count += 1

            page_cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not page_cursor:
                break
            time.sleep(0.3)  # rate limit

        logger.info("  Stored %d messages from channel %s", channel_count, channel_id)
        total_stored += channel_count

    # ── 4. Summary ───────────────────────────────────────────────────────────
    with sqlite3.connect(DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]

    logger.info("✅ Backfill complete. Stored %d new messages. Total in DB: %d", total_stored, count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Sebol memory from Slack history")
    parser.add_argument("--days", type=int, default=365, help="How many days back to fetch (default: 365)")
    args = parser.parse_args()
    run_backfill(days=args.days)
