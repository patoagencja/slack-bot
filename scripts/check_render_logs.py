#!/usr/bin/env python3
"""
Fetch recent ERROR logs from the Slack errors channel and print them.
Claude Code uses this script in /loop to detect and fix production errors.

Exit codes:
  0 — no errors found
  1 — errors found (printed to stdout for Claude to analyze)
  2 — API failure (credentials invalid, network issue, etc.)

Usage:
    python3 scripts/check_render_logs.py
    python3 scripts/check_render_logs.py --minutes 10
"""

import sys
import json
import time
import argparse
import urllib.request
import urllib.parse

import os
SLACK_BOT_TOKEN   = os.environ.get("SLACK_BOT_TOKEN", "")
ERRORS_CHANNEL_ID = os.environ.get("ERRORS_CHANNEL_ID", "C0ALWCQL97D")

# Keywords that indicate an error log line from the bot
ERROR_MARKERS = ["❌ *ERROR*", "❌ *CRITICAL*"]


def slack_get(method: str, **params) -> dict:
    params["token"] = SLACK_BOT_TOKEN
    url = f"https://slack.com/api/{method}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error')}")
    return data


def fetch_error_messages(minutes_back: int = 6) -> list[str]:
    oldest = str(time.time() - minutes_back * 60)
    data = slack_get(
        "conversations.history",
        channel=ERRORS_CHANNEL_ID,
        oldest=oldest,
        limit=50,
    )
    messages = data.get("messages", [])
    errors = []
    for msg in messages:
        text = msg.get("text", "")
        if any(marker in text for marker in ERROR_MARKERS):
            errors.append(text)
    return errors


def main():
    parser = argparse.ArgumentParser(description="Check Slack errors channel for bot errors")
    parser.add_argument("--minutes", type=int, default=6,
                        help="How many minutes back to look (default: 6)")
    args = parser.parse_args()

    try:
        errors = fetch_error_messages(args.minutes)
    except Exception as exc:
        print(f"[monitor] Could not fetch Slack messages: {exc}", file=sys.stderr)
        sys.exit(2)

    if not errors:
        print(f"[monitor] No errors in the last {args.minutes} minutes. Bot is healthy ✅")
        sys.exit(0)

    print(f"[monitor] Found {len(errors)} error(s) in the last {args.minutes} minutes:\n")
    for err in errors:
        print(err)
        print("---")
    sys.exit(1)


if __name__ == "__main__":
    main()
