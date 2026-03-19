"""
Token usage logging for Anthropic API calls.
Logs input/output tokens to SQLite, calculates PLN cost.
"""
import sqlite3
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "token_log.db")

# USD per 1M tokens (Anthropic pricing)
_PRICES = {
    "claude-opus-4":    (15.00, 75.00),
    "claude-sonnet-4":  (3.00,  15.00),
    "claude-haiku-4":   (0.25,  1.25),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku":  (0.80,  4.00),
    "claude-3-opus":    (15.00, 75.00),
}
_USD_TO_PLN = 4.0


def _get_conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cost_usd REAL NOT NULL,
            context TEXT
        )
    """)
    conn.commit()
    return conn


def _model_price(model: str):
    for key, prices in _PRICES.items():
        if key in model:
            return prices
    return (3.00, 15.00)  # default: sonnet pricing


def log_tokens(model: str, input_tokens: int, output_tokens: int, context: str = ""):
    try:
        price_in, price_out = _model_price(model)
        cost_usd = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
        ts = datetime.now(timezone.utc).isoformat()
        conn = _get_conn()
        conn.execute(
            "INSERT INTO token_log (ts, model, input_tokens, output_tokens, cost_usd, context) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, model, input_tokens, output_tokens, cost_usd, context)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("token_log failed: %s", e)


def get_cost_summary(days: int = 30) -> str:
    try:
        conn = _get_conn()
        rows = conn.execute("""
            SELECT model,
                   SUM(input_tokens)  AS inp,
                   SUM(output_tokens) AS out,
                   SUM(cost_usd)      AS cost,
                   COUNT(*)           AS calls
            FROM token_log
            WHERE ts >= datetime('now', ?)
            GROUP BY model
            ORDER BY cost DESC
        """, (f"-{days} days",)).fetchall()

        total = conn.execute("""
            SELECT SUM(input_tokens), SUM(output_tokens), SUM(cost_usd), COUNT(*)
            FROM token_log
            WHERE ts >= datetime('now', ?)
        """, (f"-{days} days",)).fetchone()
        conn.close()

        if not rows or not total[0]:
            return f"Brak danych z ostatnich {days} dni."

        lines = [f"*Token cost tracking — ostatnie {days} dni*\n"]
        for model, inp, out, cost, calls in rows:
            pln = cost * _USD_TO_PLN
            lines.append(f"• `{model}`\n  {calls} wywołań · {inp:,} in / {out:,} out · ${cost:.4f} (~{pln:.2f} PLN)")

        t_inp, t_out, t_cost, t_calls = total
        t_pln = t_cost * _USD_TO_PLN
        lines.append(f"\n*Łącznie:* {t_calls} wywołań · {t_inp:,} in / {t_out:,} out")
        lines.append(f"*Koszt: ${t_cost:.4f} (~{t_pln:.2f} PLN)*")
        return "\n".join(lines)
    except Exception as e:
        return f"Błąd odczytu token_log: {e}"


# ── Wrapper ────────────────────────────────────────────────────────────────────

class _LoggingMessages:
    """Wraps anthropic.messages — logs usage after every create() call."""

    def __init__(self, original):
        self._orig = original

    def create(self, **kwargs):
        resp = self._orig.create(**kwargs)
        try:
            log_tokens(
                model=kwargs.get("model", "unknown"),
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
            )
        except Exception:
            pass
        return resp

    def __getattr__(self, name):
        return getattr(self._orig, name)


class LoggingAnthropicWrapper:
    """Drop-in wrapper around Anthropic client that auto-logs token usage."""

    def __init__(self, client):
        self._client = client
        self.messages = _LoggingMessages(client.messages)

    def __getattr__(self, name):
        return getattr(self._client, name)
