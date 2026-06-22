"""
investing/config.py — Central configuration for the rebuilt investment skill.

This is the SINGLE source of truth for:
  * Claude model selection (P0.1 — no hardcoded model strings anywhere else)
  * risk / sizing defaults
  * setup + market-health thresholds (all calibratable later, never magic numbers
    buried in business logic)
  * data-freshness TTLs and staleness limits used by the data-quality gate
  * version stamps recorded with every recommendation (backtest reproducibility)

Every module MUST import its tunables from here. Nothing reads a model name or a
threshold from a literal in business code.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache


def _env(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val else default


# ── Claude models (P0.1) ──────────────────────────────────────────────────────
# Primary defaults to the currently-supported Claude Sonnet. Everything is
# overridable via env so we never have to touch code to roll a model forward.
CLAUDE_MODEL_PRIMARY: str = _env("CLAUDE_MODEL_PRIMARY", "claude-sonnet-4-6")
CLAUDE_MODEL_FALLBACK: str = _env("CLAUDE_MODEL_FALLBACK", "claude-sonnet-4-20250514")
CLAUDE_MODEL_FAST: str = _env("CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001")

# Max tokens for the qualitative LLM extraction call.
LLM_MAX_TOKENS: int = int(_env("INVEST_LLM_MAX_TOKENS", "2000"))
# One repair retry on schema failure (P0.2). 1 == a single corrective attempt.
LLM_SCHEMA_REPAIR_RETRIES: int = int(_env("INVEST_LLM_REPAIR_RETRIES", "1"))


# ── Strategy / horizon ─────────────────────────────────────────────────────────
STRATEGY_POSITION = "POSITION_20_90"   # the new main strategy
STRATEGY_SWING = "SWING_7D"            # legacy ~7-day scanner kept separate

HORIZON_MIN_SESSIONS: int = 20
HORIZON_MAX_SESSIONS: int = 90
HORIZON_DEFAULT_SESSIONS: int = 45


# ── Risk / sizing defaults ───────────────────────────────────────────────────
# `risk` on the command line is expressed as a PERCENT of portfolio (e.g. 0.5 -> 0.5%).
DEFAULT_RISK_PER_TRADE_PCT: float = float(_env("INVEST_DEFAULT_RISK_PCT", "0.5"))
DEFAULT_PORTFOLIO_VALUE: float = float(_env("INVEST_DEFAULT_PORTFOLIO", "100000"))

MAX_POSITION_PCT: float = float(_env("INVEST_MAX_POSITION_PCT", "10")) / 100.0      # 10%
MAX_SECTOR_PCT: float = float(_env("INVEST_MAX_SECTOR_PCT", "30")) / 100.0          # 30%
MAX_NARRATIVE_PCT: float = float(_env("INVEST_MAX_NARRATIVE_PCT", "35")) / 100.0    # 35%
# Portfolio heat = sum of (open risk to stop) across all positions, as % of equity.
MAX_PORTFOLIO_HEAT_PCT: float = float(_env("INVEST_MAX_HEAT_PCT", "6")) / 100.0     # 6%
# Correlation above this with an existing holding triggers a warning / size cut.
CORRELATION_WARN: float = float(_env("INVEST_CORR_WARN", "0.80"))

# A new position whose slippage-adjusted entry is above this many ATRs over the
# pivot is too extended to chase (BREAKOUT). Configurable & later calibrated.
MAX_CHASE_ATR: float = float(_env("INVEST_MAX_CHASE_ATR", "0.75"))

# Liquidity cap: never take more than this share of average daily $ volume.
MAX_ADV_PARTICIPATION: float = float(_env("INVEST_MAX_ADV_PCT", "1")) / 100.0       # 1%

# Slippage model (bps of price) by liquidity tier — a floor; providers may refine.
SLIPPAGE_BPS_DEFAULT: float = float(_env("INVEST_SLIPPAGE_BPS", "15"))


# ── Required reward:risk by market regime ──────────────────────────────────────
# Macro never flips every name to "wait"; instead it raises the R/R bar and
# shrinks size (see market_health / decision).
RR_MIN_BY_REGIME = {
    "BULL": 2.0,
    "CAUTION": 2.5,
    "DEFENSIVE": 3.0,
    "BEAR": 3.5,
    "UNKNOWN": 3.0,
}

# Position-size multiplier applied on top of risk budget, per regime.
SIZE_MULT_BY_REGIME = {
    "BULL": 1.0,
    "CAUTION": 0.75,
    "DEFENSIVE": 0.5,
    "BEAR": 0.25,
    "UNKNOWN": 0.5,
}


# ── Event risk ─────────────────────────────────────────────────────────────────
# Inside this window an earnings/binary event is "imminent" and a full new entry
# requires an explicit event plan.
EVENT_BLACKOUT_SESSIONS: int = int(_env("INVEST_EVENT_BLACKOUT", "10"))


# ── Data freshness (seconds) — used by the data-quality gate ──────────────────
# `fresh` = considered current; `max` = beyond this the value is STALE and cannot
# support READY_TO_ENTER for fields the strategy requires.
TTL = {
    "quote":        {"fresh": 60,      "max": 300},
    "daily_bars":   {"fresh": 30 * 60, "max": 24 * 3600},
    "fundamentals": {"fresh": 24 * 3600, "max": 7 * 24 * 3600},
    "earnings":     {"fresh": 12 * 3600, "max": 4 * 24 * 3600},
    "news":         {"fresh": 60 * 60,  "max": 24 * 3600},
    "macro":        {"fresh": 24 * 3600, "max": 14 * 24 * 3600},
    "sector":       {"fresh": 6 * 3600, "max": 24 * 3600},
    "asset_proxy":  {"fresh": 24 * 3600, "max": 14 * 24 * 3600},
}

# Minimum data-quality score (0-1) required to emit READY_TO_ENTER.
MIN_DATA_QUALITY_FOR_ENTRY: float = float(_env("INVEST_MIN_DQ", "0.80"))


# ── Gateway: retry / circuit breaker / rate limits ────────────────────────────
GATEWAY = {
    "max_retries": int(_env("INVEST_GW_RETRIES", "3")),
    "base_backoff": float(_env("INVEST_GW_BACKOFF", "0.5")),   # seconds
    "max_backoff": float(_env("INVEST_GW_MAX_BACKOFF", "8")),
    "jitter": float(_env("INVEST_GW_JITTER", "0.3")),
    "timeout": float(_env("INVEST_GW_TIMEOUT", "12")),
    # circuit breaker: open after N consecutive failures, stay open for cooldown s.
    "cb_threshold": int(_env("INVEST_GW_CB_THRESHOLD", "5")),
    "cb_cooldown": float(_env("INVEST_GW_CB_COOLDOWN", "60")),
}


# ── Concurrency (Slack) ─────────────────────────────────────────────────────────
MAX_CONCURRENT_ANALYSES: int = int(_env("INVEST_MAX_CONCURRENCY", "3"))
MAX_TICKERS_PER_MESSAGE: int = 3


# ── Market calendar ─────────────────────────────────────────────────────────────
# Pre-open brief fires this many minutes before the XNYS regular open.
PRE_OPEN_BRIEF_LEAD_MIN: int = int(_env("INVEST_BRIEF_LEAD_MIN", "45"))


# ── Database ─────────────────────────────────────────────────────────────────────
DB_PATH: str = _env(
    "INVEST_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "investing.db"),
)


# ── Version stamps (recorded with every recommendation) ───────────────────────
CONFIG_VERSION = "2.0.0"


@lru_cache(maxsize=1)
def code_version() -> str:
    """Short git SHA of the running code, for reproducible backtest records."""
    explicit = os.environ.get("INVEST_CODE_VERSION")
    if explicit:
        return explicit
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            stderr=subprocess.DEVNULL,
        )
        return sha.decode().strip()
    except Exception:
        return "unknown"


def model_version() -> str:
    """Identifier of the model stack used for a recommendation."""
    return f"{CLAUDE_MODEL_PRIMARY}|fallback={CLAUDE_MODEL_FALLBACK}"
