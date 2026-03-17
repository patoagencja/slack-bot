#!/usr/bin/env python3
"""
Fetch recent ERROR/CRITICAL logs from Render API and print them.
Used by Claude Code /loop to monitor and auto-fix production errors.

Usage:
    python3 scripts/check_render_logs.py
    python3 scripts/check_render_logs.py --minutes 10
"""

import sys
import json
import argparse
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

RENDER_API_KEY = "rnd_GzVLddk4CxMOSPZrGxEaGyMe3rhM"
SERVICE_ID = "srv-d69gp2i48b3s73b57p9g"
ERROR_KEYWORDS = ["ERROR", "CRITICAL", "Traceback", "Exception", "Error:", "FAILED"]


def fetch_logs(minutes_back: int = 5) -> list[dict]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes_back)

    params = urllib.parse.urlencode({
        "resource[]": SERVICE_ID,
        "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 100,
    })
    url = f"https://api.render.com/v1/logs?{params}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("logs", data) if isinstance(data, dict) else data


def is_error_line(text: str) -> bool:
    return any(kw in text for kw in ERROR_KEYWORDS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=5, help="How many minutes of logs to check")
    args = parser.parse_args()

    try:
        logs = fetch_logs(args.minutes)
    except Exception as e:
        print(f"[check_render_logs] Nie udało się pobrać logów: {e}", file=sys.stderr)
        sys.exit(1)

    errors = [entry for entry in logs if is_error_line(entry.get("text", ""))]

    if not errors:
        print(f"[check_render_logs] Brak błędów w ostatnich {args.minutes} minutach.")
        sys.exit(0)

    print(f"[check_render_logs] Znaleziono {len(errors)} błędów w ostatnich {args.minutes} minutach:\n")
    for entry in errors:
        ts = entry.get("timestamp", "")[:19]
        text = entry.get("text", "").strip()
        print(f"{ts}  {text}")

    # Exit code 1 signals to the loop that errors were found
    sys.exit(1)


if __name__ == "__main__":
    main()
