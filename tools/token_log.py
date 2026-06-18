"""Token usage logging — SQLite + cost calculation in PLN."""
import os
import sqlite3
import threading
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "token_usage.db")

# USD per million tokens (Anthropic pricing, 2025)
_PRICING = {
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-4-20250514":  {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-4-8":           {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "claude-haiku-4-5-20251001": {"input": 0.80,  "output": 4.00,  "cache_write": 1.00,  "cache_read": 0.08},
}
_DEFAULT_PRICING = {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}

USD_TO_PLN = float(os.environ.get("USD_TO_PLN", "4.0"))

_local = threading.local()


def set_current_user(user_id: str):
    """Call at the start of each message handler to attribute tokens to a user."""
    _local.user_id = user_id or ""


def _get_current_user() -> str:
    return getattr(_local, "user_id", "")


def init_token_log():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                  TEXT    NOT NULL,
                model               TEXT    NOT NULL,
                context             TEXT,
                user_id             TEXT    DEFAULT '',
                input_tokens        INTEGER DEFAULT 0,
                output_tokens       INTEGER DEFAULT 0,
                cache_write_tokens  INTEGER DEFAULT 0,
                cache_read_tokens   INTEGER DEFAULT 0,
                cost_usd            REAL    DEFAULT 0,
                cost_pln            REAL    DEFAULT 0
            )
        """)
        # migration: add user_id column to existing databases
        try:
            con.execute("ALTER TABLE token_usage ADD COLUMN user_id TEXT DEFAULT ''")
        except Exception:
            pass  # column already exists


def _calc_cost_usd(model, input_tokens, output_tokens, cache_write=0, cache_read=0):
    p = _PRICING.get(model, _DEFAULT_PRICING)
    return (
        input_tokens  * p["input"]       / 1_000_000
        + output_tokens * p["output"]      / 1_000_000
        + cache_write   * p["cache_write"] / 1_000_000
        + cache_read    * p["cache_read"]  / 1_000_000
    )


def log_usage(model: str, usage, context: str = "", user_id: str = ""):
    """Log token usage from an Anthropic API response.usage object."""
    try:
        input_tokens  = getattr(usage, "input_tokens",                  0) or 0
        output_tokens = getattr(usage, "output_tokens",                 0) or 0
        cache_write   = getattr(usage, "cache_creation_input_tokens",   0) or 0
        cache_read    = getattr(usage, "cache_read_input_tokens",       0) or 0

        cost_usd = _calc_cost_usd(model, input_tokens, output_tokens, cache_write, cache_read)
        cost_pln = cost_usd * USD_TO_PLN
        uid = user_id or _get_current_user()

        with sqlite3.connect(DB_PATH) as con:
            con.execute(
                """INSERT INTO token_usage
                   (ts, model, context, user_id, input_tokens, output_tokens,
                    cache_write_tokens, cache_read_tokens, cost_usd, cost_pln)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.utcnow().isoformat(), model, context, uid,
                 input_tokens, output_tokens, cache_write, cache_read,
                 cost_usd, cost_pln),
            )
    except Exception:
        pass  # never let logging crash the bot


def get_summary(days: int = 30):
    """Return (per_model_rows, totals_row) for the last `days` days."""
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT model,
                   COUNT(*)           AS calls,
                   SUM(input_tokens)  AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd)      AS cost_usd,
                   SUM(cost_pln)      AS cost_pln
            FROM token_usage
            WHERE ts >= datetime('now', ? || ' days')
            GROUP BY model
            ORDER BY cost_pln DESC
            """,
            (f"-{days}",),
        ).fetchall()

        total = con.execute(
            """
            SELECT COUNT(*)           AS calls,
                   SUM(input_tokens)  AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd)      AS cost_usd,
                   SUM(cost_pln)      AS cost_pln
            FROM token_usage
            WHERE ts >= datetime('now', ? || ' days')
            """,
            (f"-{days}",),
        ).fetchone()

        return rows, total


def get_user_summary(days: int = 30):
    """Return per-user token usage for the last `days` days, ordered by cost desc."""
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT user_id,
                   COUNT(*)           AS calls,
                   SUM(input_tokens)  AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd)      AS cost_usd,
                   SUM(cost_pln)      AS cost_pln
            FROM token_usage
            WHERE ts >= datetime('now', ? || ' days')
              AND user_id != ''
            GROUP BY user_id
            ORDER BY cost_pln DESC
            """,
            (f"-{days}",),
        ).fetchall()
        return rows


# ── Wrapper around Anthropic client ──────────────────────────────────────────

class _TrackedMessages:
    """Wraps client.messages — auto-logs usage after every .create() call."""

    def __init__(self, messages):
        self._messages = messages

    def create(self, *args, **kwargs):
        response = self._messages.create(*args, **kwargs)
        model = kwargs.get("model", args[0] if args else "unknown")
        log_usage(model, response.usage)
        return response

    def __getattr__(self, name):
        return getattr(self._messages, name)


class TrackedAnthropicClient:
    """Drop-in replacement for Anthropic() that logs token usage to SQLite."""

    def __init__(self, client):
        self._client = client
        self.messages = _TrackedMessages(client.messages)

    def __getattr__(self, name):
        return getattr(self._client, name)
