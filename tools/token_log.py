"""Token usage logging and cost reporting for Sebol."""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

_LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "token_log.jsonl")

# Ceny za 1M tokenów w USD (api.anthropic.com, 2025/2026)
_PRICING = {
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6":         {"input":  3.00, "output": 15.00},
    "claude-sonnet-4-20250514":  {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input":  0.80, "output":  4.00},
    "claude-haiku-4-5":          {"input":  0.80, "output":  4.00},
}
_DEFAULT_PRICING = {"input": 3.00, "output": 15.00}

USD_TO_PLN = 4.0


def _price(model: str, input_tokens: int, output_tokens: int) -> float:
    """Zwraca koszt w PLN."""
    p = _PRICING.get(model, _DEFAULT_PRICING)
    usd = (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000
    return round(usd * USD_TO_PLN, 6)


def log_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    """Dopisuje jeden wpis do data/token_log.jsonl."""
    try:
        os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "in": input_tokens,
            "out": output_tokens,
            "pln": _price(model, input_tokens, output_tokens),
        }
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # nigdy nie blokuj bota przez logowanie


def cost_report(days: int = 30) -> dict:
    """
    Zwraca słownik ze statystykami z ostatnich `days` dni.
    {
      "total_pln": float,
      "total_calls": int,
      "total_in": int,
      "total_out": int,
      "by_model": {model: {"calls", "in", "out", "pln"}},
      "by_day":   {YYYY-MM-DD: {"calls", "pln"}},
    }
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    total_pln = 0.0
    total_calls = 0
    total_in = 0
    total_out = 0
    by_model: dict = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "pln": 0.0})
    by_day:   dict = defaultdict(lambda: {"calls": 0, "pln": 0.0})

    if not os.path.exists(_LOG_FILE):
        return {"total_pln": 0.0, "total_calls": 0, "total_in": 0, "total_out": 0, "by_model": {}, "by_day": {}}

    with open(_LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["ts"])
                if ts < cutoff:
                    continue
                model = e.get("model", "unknown")
                inp   = e.get("in", 0)
                out   = e.get("out", 0)
                pln   = e.get("pln", 0.0)
                day   = ts.strftime("%Y-%m-%d")

                total_pln   += pln
                total_calls += 1
                total_in    += inp
                total_out   += out

                bm = by_model[model]
                bm["calls"] += 1
                bm["in"]    += inp
                bm["out"]   += out
                bm["pln"]   += pln

                bd = by_day[day]
                bd["calls"] += 1
                bd["pln"]   += pln
            except Exception:
                continue

    # zaokrąglenia
    total_pln = round(total_pln, 4)
    for m in by_model.values():
        m["pln"] = round(m["pln"], 4)
    for d in by_day.values():
        d["pln"] = round(d["pln"], 4)

    return {
        "total_pln":   total_pln,
        "total_calls": total_calls,
        "total_in":    total_in,
        "total_out":   total_out,
        "by_model":    dict(by_model),
        "by_day":      dict(sorted(by_day.items())),
    }
