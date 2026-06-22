"""
investing/persistence.py — SQLite state for the investment skill.

Replaces loose data/*.json with a transactional store. Tables:

    signals, position_plans, positions, recommendation_outcomes,
    market_health_history, api_cache, job_runs, data_quality_events

Every recommendation is stored with a full snapshot of the features used at
decision time (backtest reproducibility). All writes go through ``with conn:``
so a failure rolls back atomically.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import threading
from typing import Any, Optional

from . import config

_LOCAL = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    strategy TEXT,
    setup_type TEXT,
    signal_confidence REAL,
    data_quality_score REAL,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS position_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    strategy TEXT,
    setup_type TEXT,
    decision_status TEXT,
    decision_reason TEXT,
    entry_trigger REAL,
    stop REAL,
    target_1 REAL,
    target_2 REAL,
    target_3 REAL,
    recommended_quantity INTEGER,
    risk_budget REAL,
    market_regime TEXT,
    event_plan TEXT,
    data_quality_score REAL,
    signal_confidence REAL,
    config_version TEXT,
    code_version TEXT,
    model_version TEXT,
    feature_snapshot TEXT,
    plan_json TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    opened_at TEXT,
    entry_price REAL,
    stop_price REAL,
    quantity INTEGER,
    sector TEXT,
    narrative TEXT,
    beta REAL,
    status TEXT DEFAULT 'open',
    closed_at TEXT,
    exit_price REAL,
    plan_id INTEGER
);
CREATE TABLE IF NOT EXISTS recommendation_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER,
    ticker TEXT,
    horizon_session INTEGER,
    as_of TEXT,
    price REAL,
    mfe REAL,
    mae REAL,
    r_multiple REAL,
    hit_stop INTEGER,
    hit_target_1 INTEGER,
    hit_target_2 INTEGER,
    time_to_target INTEGER,
    gap_risk REAL,
    max_drawdown REAL,
    setup_type TEXT,
    sector TEXT,
    market_regime TEXT
);
CREATE TABLE IF NOT EXISTS market_health_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    composite REAL,
    percentile REAL,
    zscore REAL,
    regime TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS api_cache (
    key TEXT PRIMARY KEY,
    value TEXT,
    source TEXT,
    fetched_at TEXT,
    ttl_seconds REAL
);
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS data_quality_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT,
    field TEXT,
    status TEXT,
    source TEXT,
    detail TEXT
);
"""


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    cached = getattr(_LOCAL, "conn", None)
    if cached is not None and getattr(_LOCAL, "path", None) == path:
        return cached
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    _LOCAL.conn = conn
    _LOCAL.path = path
    return conn


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = connect(db_path)
    with conn:
        conn.executescript(_SCHEMA)
    return conn


# ── Position plans / signals ───────────────────────────────────────────────────
def save_position_plan(plan, db_path: Optional[str] = None) -> int:
    """Persist a PositionPlan (with full feature snapshot). Returns row id."""
    conn = init_db(db_path)
    data = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else dict(plan)
    with conn:
        cur = conn.execute(
            """INSERT INTO position_plans
               (ts, ticker, strategy, setup_type, decision_status, decision_reason,
                entry_trigger, stop, target_1, target_2, target_3,
                recommended_quantity, risk_budget, market_regime, event_plan,
                data_quality_score, signal_confidence,
                config_version, code_version, model_version, feature_snapshot, plan_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data.get("created_at") or _utcnow_iso(),
                data.get("ticker"), data.get("strategy"), data.get("setup_type"),
                data.get("decision_status"), data.get("decision_reason"),
                data.get("entry_trigger"), data.get("technical_stop"),
                data.get("target_1"), data.get("target_2"), data.get("target_3"),
                data.get("recommended_quantity"), data.get("risk_budget"),
                data.get("market_regime"), data.get("event_plan"),
                data.get("data_quality_score"), data.get("signal_confidence"),
                data.get("config_version"), data.get("code_version"),
                data.get("model_version"),
                json.dumps(data.get("feature_snapshot") or {}),
                json.dumps(data),
            ),
        )
        return int(cur.lastrowid)


def log_signal(ticker: str, strategy: str, setup_type: str, confidence: float,
               dq: float, payload: dict, db_path: Optional[str] = None) -> int:
    conn = init_db(db_path)
    with conn:
        cur = conn.execute(
            "INSERT INTO signals (ts,ticker,strategy,setup_type,signal_confidence,data_quality_score,payload)"
            " VALUES (?,?,?,?,?,?,?)",
            (_utcnow_iso(), ticker, strategy, setup_type, confidence, dq, json.dumps(payload)),
        )
        return int(cur.lastrowid)


def log_data_quality_event(ticker: str, field: str, status: str, source: str,
                           detail: str = "", db_path: Optional[str] = None) -> None:
    conn = init_db(db_path)
    with conn:
        conn.execute(
            "INSERT INTO data_quality_events (ts,ticker,field,status,source,detail) VALUES (?,?,?,?,?,?)",
            (_utcnow_iso(), ticker, field, status, source, detail),
        )


# ── Positions repository ────────────────────────────────────────────────────────
def add_position(*, ticker: str, entry_price: float, stop_price: float, quantity: int,
                 sector: str = "UNKNOWN", narrative: str = "UNKNOWN", beta: Optional[float] = None,
                 plan_id: Optional[int] = None, db_path: Optional[str] = None) -> int:
    conn = init_db(db_path)
    with conn:
        cur = conn.execute(
            """INSERT INTO positions (ticker,opened_at,entry_price,stop_price,quantity,sector,narrative,beta,status,plan_id)
               VALUES (?,?,?,?,?,?,?,?, 'open', ?)""",
            (ticker, _utcnow_iso(), entry_price, stop_price, quantity, sector, narrative, beta, plan_id),
        )
        return int(cur.lastrowid)


def close_position(position_id: int, exit_price: float, db_path: Optional[str] = None) -> None:
    conn = init_db(db_path)
    with conn:
        conn.execute("UPDATE positions SET status='closed', closed_at=?, exit_price=? WHERE id=?",
                     (_utcnow_iso(), exit_price, position_id))


def list_open_positions(db_path: Optional[str] = None) -> list[dict]:
    conn = init_db(db_path)
    rows = conn.execute("SELECT * FROM positions WHERE status='open'").fetchall()
    return [dict(r) for r in rows]


# ── Market health history ────────────────────────────────────────────────────────
def save_market_health(composite, percentile, zscore, regime, payload: dict,
                        db_path: Optional[str] = None) -> int:
    conn = init_db(db_path)
    with conn:
        cur = conn.execute(
            "INSERT INTO market_health_history (ts,composite,percentile,zscore,regime,payload) VALUES (?,?,?,?,?,?)",
            (_utcnow_iso(), composite, percentile, zscore, regime, json.dumps(payload)),
        )
        return int(cur.lastrowid)


def market_health_series(limit: int = 250, db_path: Optional[str] = None) -> list[float]:
    conn = init_db(db_path)
    rows = conn.execute(
        "SELECT composite FROM market_health_history WHERE composite IS NOT NULL ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["composite"] for r in rows][::-1]


# ── API cache (cross-process) ─────────────────────────────────────────────────────
def cache_get(key: str, db_path: Optional[str] = None) -> Optional[Any]:
    conn = init_db(db_path)
    row = conn.execute("SELECT value, fetched_at, ttl_seconds FROM api_cache WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    fetched = _dt.datetime.fromisoformat(row["fetched_at"])
    age = (_dt.datetime.now(_dt.timezone.utc) - fetched).total_seconds()
    if row["ttl_seconds"] is not None and age > row["ttl_seconds"]:
        return None
    try:
        return json.loads(row["value"])
    except Exception:
        return None


def cache_set(key: str, value: Any, ttl_seconds: float, source: str = "",
              db_path: Optional[str] = None) -> None:
    conn = init_db(db_path)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO api_cache (key,value,source,fetched_at,ttl_seconds) VALUES (?,?,?,?,?)",
            (key, json.dumps(value), source, _utcnow_iso(), ttl_seconds),
        )


# ── Job runs ───────────────────────────────────────────────────────────────────
def record_job_run(job: str, status: str, started_at: str, detail: str = "",
                   db_path: Optional[str] = None) -> None:
    conn = init_db(db_path)
    with conn:
        conn.execute(
            "INSERT INTO job_runs (job,started_at,finished_at,status,detail) VALUES (?,?,?,?,?)",
            (job, started_at, _utcnow_iso(), status, detail),
        )


# ── Outcomes (backtest tracking) ──────────────────────────────────────────────────
def save_outcome(outcome: dict, db_path: Optional[str] = None) -> int:
    conn = init_db(db_path)
    cols = ("plan_id", "ticker", "horizon_session", "as_of", "price", "mfe", "mae",
            "r_multiple", "hit_stop", "hit_target_1", "hit_target_2", "time_to_target",
            "gap_risk", "max_drawdown", "setup_type", "sector", "market_regime")
    with conn:
        cur = conn.execute(
            f"INSERT INTO recommendation_outcomes ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            tuple(outcome.get(c) for c in cols),
        )
        return int(cur.lastrowid)
