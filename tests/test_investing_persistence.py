"""SQLite persistence tests — schema, save/query, atomic rollback."""

import sqlite3

import pytest

from investing import persistence
from investing.schemas import (AssetType, DecisionStatus, PositionPlan, SetupType)


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "invest_test.db")


def test_init_creates_all_tables(db):
    conn = persistence.init_db(db)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for t in ("signals", "position_plans", "positions", "recommendation_outcomes",
              "market_health_history", "api_cache", "job_runs", "data_quality_events"):
        assert t in names


def test_save_position_plan_persists_snapshot(db):
    plan = PositionPlan(ticker="NVDA", strategy="POSITION_20_90", horizon_sessions=45,
                        decision_status=DecisionStatus.READY_TO_ENTER, setup_type=SetupType.BREAKOUT,
                        asset_type=AssetType.EQUITY, entry_trigger=101.0, technical_stop=99.5,
                        recommended_quantity=80, feature_snapshot={"rs": {"rs63_broad": 8.0}})
    pid = persistence.save_position_plan(plan, db_path=db)
    conn = persistence.connect(db)
    row = conn.execute("SELECT * FROM position_plans WHERE id=?", (pid,)).fetchone()
    assert row["ticker"] == "NVDA"
    assert row["decision_status"] == "READY_TO_ENTER"
    import json
    snap = json.loads(row["feature_snapshot"])
    assert snap["rs"]["rs63_broad"] == 8.0


def test_positions_repository_roundtrip(db):
    persistence.init_db(db)
    pid = persistence.add_position(ticker="AMD", entry_price=100, stop_price=90,
                                   quantity=50, sector="AI/Semis", narrative="AI", db_path=db)
    openp = persistence.list_open_positions(db)
    assert len(openp) == 1 and openp[0]["ticker"] == "AMD"
    persistence.close_position(pid, exit_price=120, db_path=db)
    assert persistence.list_open_positions(db) == []


def test_cache_respects_ttl(db):
    persistence.init_db(db)
    persistence.cache_set("k", {"v": 1}, ttl_seconds=1000, db_path=db)
    assert persistence.cache_get("k", db) == {"v": 1}
    persistence.cache_set("k2", {"v": 2}, ttl_seconds=-1, db_path=db)  # already expired
    assert persistence.cache_get("k2", db) is None


def test_atomic_rollback_on_error(db):
    conn = persistence.init_db(db)
    before = conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"]
    with pytest.raises(sqlite3.Error):
        with conn:  # transaction; should roll back fully on the second, bad insert
            conn.execute("INSERT INTO signals (ts,ticker) VALUES ('now','OK')")
            conn.execute("INSERT INTO nonexistent_table (x) VALUES (1)")
    after = conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"]
    assert after == before  # the first insert was rolled back
