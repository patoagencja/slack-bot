"""
Performance analysis, campaign trends, benchmarks, self-learning system,
and /ads command dispatching.
"""
import os
import json
import logging
import inspect
import calendar
from datetime import datetime, timedelta

import _ctx
from config.constants import HISTORY_FILE, HISTORY_RETENTION_DAYS, AD_CLIENTS, CHANNEL_CLIENT_MAP
from tools.meta_ads import meta_ads_tool
from tools.google_ads import google_ads_tool

logger = logging.getLogger(__name__)


# ── CAMPAIGN OBJECTIVE DETECTION ──────────────────────────────────────────────

def _detect_campaign_objective(campaign_name):
    """Wykrywa cel kampanii z nazwy. Zwraca: 'engagement'|'reach'|'traffic'|'conversion'."""
    n = (campaign_name or '').lower()
    if any(k in n for k in ['engagement', 'zaangaż', 'zaangaz', 'interakcj', 'reakcj', 'post eng']):
        return 'engagement'
    if any(k in n for k in ['reach', 'zasięg', 'zasieg', 'awareness', 'świadom', 'swiadom', 'brand']):
        return 'reach'
    if any(k in n for k in ['traffic', 'ruch', 'link click', 'link_click', 'kliknięcia', 'klikniecia']):
        return 'traffic'
    return 'conversion'


def _extract_engagement_actions(campaign_data):
    """Wyciąga engagement metryki z Meta actions list."""
    actions_raw = campaign_data.get('actions') or []
    by_type = {}
    for a in actions_raw:
        action_type = a.get('action_type', '')
        try:
            by_type[action_type] = int(float(a.get('value', 0) or 0))
        except (ValueError, TypeError):
            by_type[action_type] = 0
    return {
        'reactions':  by_type.get('post_reaction', 0),
        'comments':   by_type.get('comment', 0),
        'post_saves': by_type.get('onsite_conversion.post_save', 0),
        'shares':     by_type.get('post', 0),
    }


# ── ADS CLIENT RESOLUTION ─────────────────────────────────────────────────────

def _resolve_ads_client(channel_id, text):
    """Zwraca (client_key, client_cfg). Szuka nazwy w tekście, potem mapuje z kanału."""
    text_lower = (text or "").strip().lower()
    for key in AD_CLIENTS:
        if key in text_lower:
            return key, AD_CLIENTS[key]
    if channel_id in CHANNEL_CLIENT_MAP:
        key = CHANNEL_CLIENT_MAP[channel_id]
        return key, AD_CLIENTS[key]
    return None, None


def _parse_period(text, default=7):
    """Wyciąga liczbę dni z tekstu, np. '3d' → 3."""
    import re
    m = re.search(r'\b(\d+)d\b', (text or "").lower())
    if m:
        return max(1, min(int(m.group(1)), 90))
    return default


def _fetch_ads_data(client_cfg, date_from, date_to, min_spend=20.0):
    """Pobiera dane Meta + Google dla klienta, zwraca unified listę kampanii."""
    campaigns = []
    try:
        meta = meta_ads_tool(
            client_name=client_cfg["meta_name"],
            date_from=date_from, date_to=date_to,
            level="campaign",
            metrics=["campaign_name", "spend", "impressions", "clicks", "ctr",
                     "cpc", "reach", "frequency", "purchase_roas", "conversions"],
        )
        for c in meta.get("data", []):
            if float(c.get("spend", 0) or 0) >= min_spend:
                c["platform"] = "meta"
                campaigns.append(c)
    except Exception as _e:
        logger.error(f"_fetch_ads_data meta error: {_e}")

    for account in client_cfg.get("google_accounts", []):
        try:
            gdata = google_ads_tool(
                client_name=account,
                date_from=date_from, date_to=date_to,
                level="campaign",
                metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                         "metrics.cost_micros", "metrics.conversions",
                         "metrics.ctr", "metrics.average_cpc"],
            )
            for c in gdata.get("data", []):
                if float(c.get("cost", c.get("spend", 0)) or 0) >= min_spend:
                    c["platform"] = "google"
                    campaigns.append(c)
        except Exception as _e:
            logger.error(f"_fetch_ads_data google error ({account}): {_e}")

    return campaigns


# ── BENCHMARKS & HISTORY ───────────────────────────────────────────────────────

def check_conversion_history(client_name, platform, campaign_name, lookback_days=30):
    """Sprawdza czy kampania kiedykolwiek miała conversions w historii."""
    try:
        date_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        date_to   = datetime.now().strftime('%Y-%m-%d')

        if platform == "meta":
            data = meta_ads_tool(client_name=client_name, date_from=date_from, date_to=date_to,
                                 level="campaign", campaign_name=campaign_name,
                                 metrics=["campaign_name", "conversions"])
            if data.get("data"):
                total = sum(item.get("conversions", 0) for item in data["data"])
                return {"had_conversions": total > 0, "total": total,
                        "alert_level": "CRITICAL" if total > 0 else "WARNING"}

        elif platform == "google":
            data = google_ads_tool(client_name=client_name, date_from=date_from, date_to=date_to,
                                   level="campaign", campaign_name=campaign_name,
                                   metrics=["campaign.name", "metrics.conversions"])
            if data.get("data"):
                total = sum(item.get("conversions", 0) for item in data["data"])
                return {"had_conversions": total > 0, "total": total,
                        "alert_level": "CRITICAL" if total > 0 else "WARNING"}

        return {"had_conversions": False, "total": 0, "alert_level": "WARNING"}
    except Exception as e:
        logger.error(f"Błąd sprawdzania historii: {e}")
        return {"had_conversions": False, "total": 0, "alert_level": "WARNING"}


def get_client_benchmarks(client_name, platform, lookback_days=30):
    """Pobiera benchmarki (30-dniowe średnie) dla klienta."""
    try:
        date_to   = datetime.now().strftime('%Y-%m-%d')
        date_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        if platform == "meta":
            data = meta_ads_tool(client_name=client_name, date_from=date_from, date_to=date_to,
                                 level="campaign",
                                 metrics=["campaign_name", "spend", "impressions", "clicks",
                                          "ctr", "cpc", "purchase_roas", "frequency", "conversions"])
            campaigns = data.get("data", [])
            if not campaigns:
                return None
            ctrs  = [c["ctr"] for c in campaigns if c.get("ctr")]
            cpcs  = [c["cpc"] for c in campaigns if c.get("cpc")]
            roases = [c["purchase_roas"] for c in campaigns if c.get("purchase_roas")]
            freqs  = [c["frequency"] for c in campaigns if c.get("frequency")]
            return {
                "avg_ctr":       sum(ctrs)  / len(ctrs)   if ctrs   else None,
                "avg_cpc":       sum(cpcs)  / len(cpcs)   if cpcs   else None,
                "avg_roas":      sum(roases)/len(roases)  if roases else None,
                "avg_frequency": sum(freqs) / len(freqs)  if freqs  else None,
                "period_days":   lookback_days,
                "campaign_count": len(campaigns),
            }

        elif platform == "google":
            all_campaigns = []
            for account in ["dre", "dre 2024", "dre 2025"]:
                gdata = google_ads_tool(client_name=account, date_from=date_from, date_to=date_to,
                                        level="campaign",
                                        metrics=["campaign.name", "metrics.impressions",
                                                 "metrics.clicks", "metrics.cost_micros",
                                                 "metrics.conversions", "metrics.ctr",
                                                 "metrics.average_cpc"])
                if gdata.get("data"):
                    all_campaigns.extend(gdata["data"])
            if not all_campaigns:
                return None
            ctrs = [c["ctr"] for c in all_campaigns if c.get("ctr")]
            cpcs = [c["cpc"] for c in all_campaigns if c.get("cpc")]
            return {
                "avg_ctr":       sum(ctrs)/len(ctrs) if ctrs else None,
                "avg_cpc":       sum(cpcs)/len(cpcs) if cpcs else None,
                "avg_roas":      None,
                "avg_frequency": None,
                "period_days":   lookback_days,
                "campaign_count": len(all_campaigns),
            }
    except Exception as e:
        logger.error(f"Błąd pobierania benchmarków: {e}")
        return None


def _benchmark_flag(current, benchmark, higher_is_better=True):
    """Zwraca emoji + % różnicy vs benchmark."""
    if benchmark is None or benchmark == 0 or current is None:
        return ""
    diff_pct = (current - benchmark) / benchmark * 100
    if not higher_is_better:
        diff_pct = -diff_pct
    flag = "🟢" if diff_pct >= 20 else ("✅" if diff_pct >= -10 else ("🟡" if diff_pct >= -20 else "🔴"))
    sign = "+" if diff_pct >= 0 else ""
    return f" {flag} (avg: {benchmark:.2f}, {sign}{diff_pct:.0f}%)"


# ── CLAUDE TREND ANALYSIS ──────────────────────────────────────────────────────

def analyze_campaign_trends(campaigns_data, lookback_days=7, goal="conversion",
                            meta_benchmarks=None, google_benchmarks=None):
    """Claude analizuje kampanie holistycznie. Zwraca critical_alerts, warnings, top_performers."""
    if not campaigns_data:
        return {"critical_alerts": [], "warnings": [], "top_performers": [], "goal": goal}

    campaigns_data = [c for c in campaigns_data
                      if float(c.get("spend") or c.get("cost") or 0) >= 20]
    if not campaigns_data:
        return {"critical_alerts": [], "warnings": [], "top_performers": [], "goal": goal}

    campaigns_txt = ""
    for c in campaigns_data:
        name  = c.get("campaign_name") or c.get("name", "?")
        spend = c.get("spend") or c.get("cost", 0) or 0
        ctr   = c.get("ctr", 0) or 0
        cpc   = c.get("cpc") or c.get("average_cpc", 0) or 0
        roas  = c.get("purchase_roas", 0) or 0
        convs = c.get("conversions", 0) or 0
        freq  = c.get("frequency", 0) or 0
        reach = c.get("reach", 0) or 0
        impr  = c.get("impressions", 0) or 0
        clicks = c.get("clicks", 0) or 0
        platform = c.get("platform", "meta")

        campaigns_txt += (f"- [{platform.upper()}] {name}: spend={spend:.0f}PLN ctr={ctr:.2f}% "
                          f"cpc={cpc:.2f}PLN")
        if goal == "conversion":
            campaigns_txt += f" roas={roas:.2f} conv={convs}"
        campaigns_txt += f" freq={freq:.1f} reach={reach:,} impr={impr:,} clicks={clicks:,}\n"

    goal_context = (
        "Klient robi kampanie ENGAGEMENT/TRAFFIC (nie e-commerce). Ważne: CTR, CPC, reach, frequency. "
        "NIE oceniaj konwersji ani ROAS."
        if goal == "engagement" else
        "Klient robi kampanie CONVERSION/E-COMMERCE. Ważne: ROAS, konwersje, CPA, CTR."
    )

    benchmarks_txt = ""
    if meta_benchmarks:
        b = meta_benchmarks
        lines = []
        if b.get("avg_ctr") is not None: lines.append(f"CTR={b['avg_ctr']:.2f}%")
        if b.get("avg_cpc") is not None: lines.append(f"CPC={b['avg_cpc']:.2f}PLN")
        if b.get("avg_roas") is not None: lines.append(f"ROAS={b['avg_roas']:.2f}x")
        if b.get("avg_frequency") is not None: lines.append(f"freq={b['avg_frequency']:.1f}")
        if lines:
            benchmarks_txt += f"META (ostatnie {b.get('period_days', 30)} dni): {' | '.join(lines)}\n"
    if google_benchmarks:
        b = google_benchmarks
        lines = []
        if b.get("avg_ctr") is not None: lines.append(f"CTR={b['avg_ctr']:.2f}%")
        if b.get("avg_cpc") is not None: lines.append(f"CPC={b['avg_cpc']:.2f}PLN")
        if lines:
            benchmarks_txt += f"GOOGLE (ostatnie {b.get('period_days', 30)} dni): {' | '.join(lines)}\n"

    benchmark_section = ""
    if benchmarks_txt:
        benchmark_section = (
            f"\nHistoryczne benchmarki (30-dniowe średnie):\n{benchmarks_txt}\n"
            "Porównaj wyniki do tych benchmarków. Wskazuj odchylenia z procentami.\n"
        )

    prompt = f"""Jesteś senior performance marketerem analizującym wyniki kampanii z wczoraj.

Kontekst klienta: {goal_context}
{benchmark_section}
Dane kampanii (tylko te z min. 20 PLN spend):
{campaigns_txt}

Przeanalizuj CAŁOŚCIOWO. Zwróć TYLKO JSON:
{{
  "critical_alerts": [{{"campaign": "nazwa", "message": "problem z liczbami", "action": "co zrobić"}}],
  "warnings": [{{"campaign": "nazwa", "message": "co sprawdzić"}}],
  "top_performers": [{{"campaign": "nazwa", "metrics_line": "kluczowe metryki"}}]
}}

Max: 3 critical, 3 warnings, 3 top performers. Bądź konkretny z liczbami."""

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        import re as _re
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not m:
            return {"critical_alerts": [], "warnings": [], "top_performers": [], "goal": goal}
        data = json.loads(m.group())
        data["goal"] = goal
        return data
    except Exception as e:
        logger.error(f"❌ Błąd analyze_campaign_trends (Claude): {e}")
        return {"critical_alerts": [], "warnings": [], "top_performers": [], "goal": goal}


# ── SELF-LEARNING SYSTEM ───────────────────────────────────────────────────────

def _load_history_raw():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_history_raw(data):
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Błąd zapisu historii: {e}")


def save_campaign_results(client, campaign, metrics, actions_taken=None):
    """Zapisuje wyniki kampanii do historii (90-dniowy retention)."""
    if actions_taken is None:
        actions_taken = []
    data = _load_history_raw()
    data.setdefault(client, {"campaigns": {}, "predictions": []})
    data[client].setdefault("campaigns", {})
    data[client].setdefault("predictions", [])

    today = datetime.now().strftime('%Y-%m-%d')
    dow   = datetime.now().strftime('%A').lower()
    entry = {
        "date": today, "day_of_week": dow,
        "is_weekend": dow in ["saturday", "sunday"],
        "ctr": metrics.get("ctr"), "cpc": metrics.get("cpc"),
        "roas": metrics.get("roas"), "frequency": metrics.get("frequency"),
        "spend": metrics.get("spend", 0), "conversions": metrics.get("conversions", 0),
        "impressions": metrics.get("impressions", 0), "clicks": metrics.get("clicks", 0),
        "platform": metrics.get("platform", "meta"), "actions_taken": actions_taken,
    }

    data[client]["campaigns"].setdefault(campaign, [])
    data[client]["campaigns"][campaign] = [
        e for e in data[client]["campaigns"][campaign] if e.get("date") != today
    ]
    data[client]["campaigns"][campaign].append(entry)

    cutoff = (datetime.now() - timedelta(days=HISTORY_RETENTION_DAYS)).strftime('%Y-%m-%d')
    data[client]["campaigns"][campaign] = [
        e for e in data[client]["campaigns"][campaign] if e.get("date", "") >= cutoff
    ]
    _save_history_raw(data)


def backfill_campaign_history(client: str, days_back: int = 90):
    """
    Fetch historical per-day campaign data from Meta Ads and store it locally.
    Skips days that already have data. Safe to call repeatedly.
    """
    date_from = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    date_to   = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    logger.info(f"[backfill] {client}: fetching {date_from} → {date_to}")
    result = meta_ads_tool(
        date_from=date_from,
        date_to=date_to,
        level="campaign",
        client_name=client,
        metrics=[
            'campaign_name', 'spend', 'impressions', 'clicks',
            'ctr', 'cpc', 'frequency', 'purchase_roas', 'conversions',
        ],
    )

    if isinstance(result, dict) and ("error" in result or "message" in result):
        logger.warning(f"[backfill] {client}: {result}")
        return

    rows = result if isinstance(result, list) else result.get("data", [])
    saved = 0
    for row in rows:
        date = row.get("date_start")
        campaign_name = row.get("campaign_name")
        if not date or not campaign_name:
            continue
        metrics = {
            "ctr":         row.get("ctr"),
            "cpc":         row.get("cpc"),
            "roas":        row.get("purchase_roas"),
            "frequency":   row.get("frequency"),
            "spend":       row.get("spend", 0),
            "conversions": row.get("conversions", 0),
            "impressions": row.get("impressions", 0),
            "clicks":      row.get("clicks", 0),
            "platform":    "meta",
        }
        # save_campaign_results uses today's date — override directly
        raw = _load_history_raw()
        raw.setdefault(client, {"campaigns": {}, "predictions": []})
        raw[client].setdefault("campaigns", {})
        raw[client]["campaigns"].setdefault(campaign_name, [])
        # Skip if entry for this date already exists
        existing = raw[client]["campaigns"][campaign_name]
        if any(e.get("date") == date for e in existing):
            continue
        dow = datetime.strptime(date, '%Y-%m-%d').strftime('%A').lower()
        entry = {
            "date": date, "day_of_week": dow,
            "is_weekend": dow in ["saturday", "sunday"],
            **metrics,
            "actions_taken": [],
        }
        existing.append(entry)
        # Trim retention
        cutoff = (datetime.now() - timedelta(days=HISTORY_RETENTION_DAYS)).strftime('%Y-%m-%d')
        raw[client]["campaigns"][campaign_name] = [
            e for e in existing if e.get("date", "") >= cutoff
        ]
        _save_history_raw(raw)
        saved += 1

    logger.info(f"[backfill] {client}: saved {saved} new day-rows")


def load_campaign_history(client, campaign=None, days_back=30):
    """Loads campaign history. Returns list (single) or dict (all)."""
    data = _load_history_raw()
    campaigns = data.get(client, {}).get("campaigns", {})
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

    if campaign:
        return [e for e in campaigns.get(campaign, []) if e.get("date", "") >= cutoff]
    return {
        name: [e for e in entries if e.get("date", "") >= cutoff]
        for name, entries in campaigns.items()
        if any(e.get("date", "") >= cutoff for e in entries)
    }


def _save_prediction(client, campaign, recommendation, predicted_metric,
                     predicted_change_pct, confidence):
    """Saves prediction for later accuracy evaluation."""
    data = _load_history_raw()
    data.setdefault(client, {"campaigns": {}, "predictions": []})
    data[client].setdefault("predictions", [])
    data[client]["predictions"].append({
        "date": datetime.now().strftime('%Y-%m-%d'),
        "campaign": campaign, "recommendation": recommendation,
        "predicted_metric": predicted_metric,
        "predicted_change_pct": predicted_change_pct,
        "confidence": confidence,
        "actual_change_pct": None, "verified": False,
    })
    cutoff = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    data[client]["predictions"] = [
        p for p in data[client]["predictions"] if p.get("date", "") >= cutoff
    ]
    _save_history_raw(data)


def calculate_confidence(pattern_count, success_count):
    """Returns confidence 0.0–1.0. Requires ≥2 observations to be nonzero."""
    if pattern_count < 2:
        return 0.0
    return (success_count / pattern_count) * min(pattern_count / 5.0, 1.0)


def analyze_patterns(client):
    """Analyzes 90-day history for frequency/creative, budget, and weekend patterns."""
    all_history = load_campaign_history(client, days_back=90)
    freq_creative = []
    budget_impact = []
    weekend_wd, weekend_we = [], []
    ctr_recovery = []

    for campaign, entries in all_history.items():
        if len(entries) < 3:
            continue
        entries_s = sorted(entries, key=lambda x: x.get("date", ""))

        for i in range(1, len(entries_s)):
            prev = entries_s[i - 1]
            curr = entries_s[i]

            if curr.get("ctr"):
                bucket = weekend_we if curr.get("is_weekend") else weekend_wd
                bucket.append({"ctr": curr["ctr"], "roas": curr.get("roas"), "campaign": campaign})

            if (prev.get("frequency", 0) >= 4.5
                    and "creative_refresh" in curr.get("actions_taken", [])
                    and i + 1 < len(entries_s)):
                after = entries_s[i + 1]
                if prev.get("ctr") and after.get("ctr") and prev["ctr"] > 0:
                    imp = (after["ctr"] - prev["ctr"]) / prev["ctr"] * 100
                    freq_creative.append({
                        "campaign": campaign, "freq_trigger": prev["frequency"],
                        "improvement_pct": imp, "success": imp > 0,
                    })

            if prev.get("spend", 0) > 0 and curr.get("spend"):
                spend_chg = (curr["spend"] - prev["spend"]) / prev["spend"] * 100
                if spend_chg > 20 and prev.get("cpc") and curr.get("cpc"):
                    cpc_chg = (curr["cpc"] - prev["cpc"]) / prev["cpc"] * 100
                    budget_impact.append({
                        "campaign": campaign, "spend_increase_pct": spend_chg,
                        "cpc_change_pct": cpc_chg, "success": cpc_chg < 10,
                    })

            for action in curr.get("actions_taken", []):
                if prev.get("ctr") and curr.get("ctr") and prev["ctr"] > 0:
                    chg = (curr["ctr"] - prev["ctr"]) / prev["ctr"] * 100
                    ctr_recovery.append({
                        "campaign": campaign, "action": action,
                        "ctr_change_pct": chg, "success": chg > 5,
                    })

    summary = {}
    if freq_creative:
        successes = [p for p in freq_creative if p["success"]]
        avg_imp = sum(p["improvement_pct"] for p in successes) / len(successes) if successes else 0
        summary["frequency_creative"] = {
            "total": len(freq_creative), "successes": len(successes),
            "avg_ctr_improvement_pct": avg_imp,
            "confidence": calculate_confidence(len(freq_creative), len(successes)),
        }
    if budget_impact:
        successes = [p for p in budget_impact if p["success"]]
        summary["budget_increase"] = {
            "total": len(budget_impact), "successes": len(successes),
            "confidence": calculate_confidence(len(budget_impact), len(successes)),
        }
    if weekend_wd and weekend_we:
        avg_wd_ctr = sum(d["ctr"] for d in weekend_wd) / len(weekend_wd)
        avg_we_ctr = sum(d["ctr"] for d in weekend_we) / len(weekend_we)
        wd_roas = [d["roas"] for d in weekend_wd if d.get("roas")]
        we_roas = [d["roas"] for d in weekend_we if d.get("roas")]
        avg_wd_roas = sum(wd_roas) / len(wd_roas) if wd_roas else 0
        avg_we_roas = sum(we_roas) / len(we_roas) if we_roas else 0
        summary["weekend"] = {
            "weekday_avg_ctr":  avg_wd_ctr,
            "weekend_avg_ctr":  avg_we_ctr,
            "ctr_diff_pct":  (avg_we_ctr - avg_wd_ctr) / avg_wd_ctr * 100 if avg_wd_ctr else 0,
            "weekday_avg_roas": avg_wd_roas,
            "weekend_avg_roas": avg_we_roas,
            "roas_diff_pct": (avg_we_roas - avg_wd_roas) / avg_wd_roas * 100 if avg_wd_roas else 0,
        }

    return {
        "freq_creative_data": freq_creative, "budget_impact_data": budget_impact,
        "weekend_wd": weekend_wd, "weekend_we": weekend_we,
        "ctr_recovery": ctr_recovery, "summary": summary,
    }


def _confidence_label(conf):
    if conf >= 0.90: return f"Strongly recommend ({conf * 100:.0f}%)"
    elif conf >= 0.70: return f"Recommend ({conf * 100:.0f}%)"
    elif conf >= 0.50: return f"Consider ({conf * 100:.0f}%)"
    return None


def generate_smart_recommendations(client, current_campaigns, patterns=None):
    """Generates ranked recommendations based on current metrics + learned patterns."""
    if patterns is None:
        patterns = analyze_patterns(client)

    recs = []
    freq_p = patterns.get("summary", {}).get("frequency_creative", {})

    for c in current_campaigns:
        name  = c.get("campaign_name", c.get("name", ""))
        if not name:
            continue
        obj   = c.get("_objective", _detect_campaign_objective(name))
        freq  = c.get("frequency")
        ctr   = c.get("ctr")
        roas  = c.get("purchase_roas", c.get("roas"))
        cpc   = c.get("cpc")
        spend = c.get("spend", c.get("cost", 0))

        # Frequency → Creative Refresh (all objectives)
        if freq and freq >= 4.5:
            avg_imp = freq_p.get("avg_ctr_improvement_pct", 30.0)
            base = freq_p.get("confidence", 0.0) if freq_p.get("total", 0) >= 2 else 0.0
            conf = min(base + 0.30 + (freq - 4.5) * 0.05, 0.95)
            if conf >= 0.50:
                hist_note = (
                    f"{freq_p.get('successes', '?')}/{freq_p.get('total', '?')} razy CTR +{avg_imp:.0f}%"
                    if freq_p.get("total") else "benchmark branżowy"
                )
                recs.append({
                    "campaign": name, "action": "Wymień kreacje (Creative Refresh)",
                    "reason": f"Frequency {freq:.1f} ≥ 4.5 – ryzyko ad fatigue",
                    "evidence": hist_note, "expected_impact": f"CTR +{avg_imp * 0.7:.0f}%–{avg_imp * 1.3:.0f}%",
                    "confidence": conf, "urgency": "🔴" if freq >= 6.0 else "🟡",
                    "predicted_metric": "ctr", "predicted_change_pct": avg_imp,
                })

        # Low CTR (only traffic + conversion)
        if ctr is not None and ctr < 0.6 and obj in ('traffic', 'conversion'):
            recs.append({
                "campaign": name, "action": "Zmień targeting / grupę odbiorców",
                "reason": f"CTR {ctr:.2f}% < 0.6%",
                "evidence": "Mismatching audience lub ad fatigue",
                "expected_impact": "CTR +0.3-0.8 pp", "confidence": 0.72, "urgency": "🟡",
                "predicted_metric": "ctr", "predicted_change_pct": 50.0,
            })

        # ROAS below break-even (only conversion)
        if roas is not None and roas < 1.5 and spend > 50 and obj == 'conversion':
            recs.append({
                "campaign": name, "action": "Pause lub głęboka optymalizacja",
                "reason": f"ROAS {roas:.2f}x – poniżej break-even (marża 40%)",
                "evidence": "ROAS <1.5x = strata na każdej transakcji",
                "expected_impact": "Oszczędność lub ROAS +60%", "confidence": 0.80, "urgency": "🔴",
                "predicted_metric": "roas", "predicted_change_pct": 60.0,
            })

        # High CPC (traffic ≤ 4 PLN, conversion ≤ 15 PLN)
        _cpc_threshold = 4 if obj == 'traffic' else 15
        if cpc is not None and cpc > _cpc_threshold and obj in ('traffic', 'conversion'):
            recs.append({
                "campaign": name, "action": "Zmień strategię bidowania (Target CPA)",
                "reason": f"CPC {cpc:.2f} PLN > {_cpc_threshold} PLN",
                "evidence": "Target CPA zazwyczaj obniża CPC o 20-30%",
                "expected_impact": "CPC -20-30%", "confidence": 0.65, "urgency": "🟡",
                "predicted_metric": "cpc", "predicted_change_pct": -25.0,
            })

    # Weekend dayparting
    weekend = patterns.get("summary", {}).get("weekend", {})
    if weekend and weekend.get("roas_diff_pct", 0) > 10:
        diff = weekend["roas_diff_pct"]
        recs.append({
            "campaign": "WSZYSTKIE kampanie", "action": "Dayparting – zwiększ budżet w weekendy",
            "reason": f"ROAS w weekendy +{diff:.0f}% vs dni robocze",
            "evidence": (f"Weekday avg ROAS: {weekend['weekday_avg_roas']:.2f}x | "
                         f"Weekend: {weekend['weekend_avg_roas']:.2f}x"),
            "expected_impact": f"+{diff * 0.4:.0f}% efektywności",
            "confidence": min(0.50 + abs(diff) / 100, 0.85), "urgency": "💡",
            "predicted_metric": "roas", "predicted_change_pct": diff * 0.4,
        })

    recs.sort(key=lambda x: x["confidence"], reverse=True)
    return [r for r in recs if r["confidence"] >= 0.50]


def suggest_experiments(client, current_campaigns):
    """Suggests A/B tests for placements/features never tried before."""
    all_history = load_campaign_history(client, days_back=90)
    known_names = set()
    for camp_list in all_history.values():
        for entry in camp_list:
            known_names.add(entry.get("campaign_name", "").lower())
    for c in current_campaigns:
        known_names.add(c.get("campaign_name", c.get("name", "")).lower())

    experiment_pool = [
        {"name": "Instagram Reels", "keywords": ["reels"],
         "expected": "CTR 1.8-2.5%", "budget": "200 PLN / 7 dni",
         "reason": "Reels mają ~40% niższy CPM vs feed – nigdy niespróbowane dla DRE"},
        {"name": "Stories", "keywords": ["stories", "story"],
         "expected": "CTR 1.5-2.0%", "budget": "150 PLN / 7 dni",
         "reason": "Stories świetne dla produktów fizycznych"},
        {"name": "Advantage+ Shopping Campaign", "keywords": ["advantage", "adv+", "asc"],
         "expected": "ROAS +30-50% vs standard", "budget": "300 PLN / 14 dni",
         "reason": "ASC automatycznie optymalizuje kreacje i targeting"},
        {"name": "Google Performance Max", "keywords": ["pmax", "performance max"],
         "expected": "Szerszy zasięg (Search+Display+YouTube)", "budget": "500 PLN / 14 dni",
         "reason": "PMax pokrywa wszystkie kanały Google jednocześnie"},
    ]

    suggestions = []
    for exp in experiment_pool:
        tested = any(any(kw in n for kw in exp["keywords"]) for n in known_names)
        if not tested:
            suggestions.append({
                "experiment": f"Test: {exp['name']}", "reason": exp["reason"],
                "expected": exp["expected"], "budget": exp["budget"], "confidence": 0.70,
            })
    return suggestions[:3]


# ── ADS COMMAND FUNCTIONS ──────────────────────────────────────────────────────

def _ads_health(client_key, client_cfg, days=7):
    date_to   = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    date_from = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    period_label = f"ostatnie {days}d ({date_from} — {date_to})"
    campaigns = _fetch_ads_data(client_cfg, date_from, date_to)
    if not campaigns:
        return f"⚠️ Brak danych ({period_label}) dla *{client_cfg['display_name']}*"

    bm    = get_client_benchmarks(client_cfg["meta_name"], "meta", 30)
    bgoog = get_client_benchmarks(client_key, "google", 30)

    total_spend  = sum(float(c.get("spend") or c.get("cost") or 0) for c in campaigns)
    total_clicks = sum(int(c.get("clicks") or 0) for c in campaigns)
    total_impr   = sum(int(c.get("impressions") or 0) for c in campaigns)
    avg_ctr = (total_clicks / total_impr * 100) if total_impr else 0
    avg_cpc_vals = [float(c.get("cpc") or c.get("average_cpc") or 0)
                    for c in campaigns if c.get("cpc") or c.get("average_cpc")]
    avg_cpc = sum(avg_cpc_vals) / len(avg_cpc_vals) if avg_cpc_vals else 0

    b_ctr = (bm or {}).get("avg_ctr")
    b_cpc = (bm or {}).get("avg_cpc")

    def _vs(val, benchmark, higher_is_better=True):
        if not benchmark or not val:
            return ""
        diff = (val - benchmark) / benchmark * 100
        if not higher_is_better:
            diff = -diff
        return f" {'🟢' if diff > 10 else ('🔴' if diff < -10 else '✅')} vs avg {benchmark:.2f}"

    n_alerts = len(analyze_campaign_trends(
        campaigns, goal=client_cfg["goal"],
        meta_benchmarks=bm, google_benchmarks=bgoog
    ).get("critical_alerts", []))
    status = "🟢 Zdrowe" if n_alerts == 0 else f"🔴 {n_alerts} alert{'y' if n_alerts > 1 else ''}"

    return (
        f"🏥 *Health — {client_cfg['display_name']}* ({period_label})\n"
        f"Status: *{status}*\n"
        f"💰 Spend: *{total_spend:.0f} PLN* | 📈 Kampanie: *{len(campaigns)}*\n"
        f"CTR: *{avg_ctr:.2f}%*{_vs(avg_ctr, b_ctr)} | "
        f"CPC: *{avg_cpc:.2f} PLN*{_vs(avg_cpc, b_cpc, higher_is_better=False)}"
    )


def _ads_anomalies(client_key, client_cfg, days=7):
    date_to   = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    date_from = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    period_label = f"ostatnie {days}d ({date_from} — {date_to})"
    campaigns = _fetch_ads_data(client_cfg, date_from, date_to)
    if not campaigns:
        return f"⚠️ Brak danych ({period_label}) dla *{client_cfg['display_name']}*"

    bm    = get_client_benchmarks(client_cfg["meta_name"], "meta", 30)
    bgoog = get_client_benchmarks(client_key, "google", 30)
    analysis = analyze_campaign_trends(campaigns, goal=client_cfg["goal"],
                                       meta_benchmarks=bm, google_benchmarks=bgoog)

    alerts   = analysis.get("critical_alerts", [])
    warnings = analysis.get("warnings", [])
    if not alerts and not warnings:
        return f"✅ *Anomalie — {client_cfg['display_name']}* ({period_label})\nBrak anomalii."

    msg = f"🔍 *Anomalie — {client_cfg['display_name']}* ({period_label})\n"
    if alerts:
        msg += "\n*🔴 Krytyczne:*\n"
        for a in alerts:
            msg += f"• *{a['campaign']}* — {a['message']}\n"
            if a.get("action"):
                msg += f"  → {a['action']}\n"
    if warnings:
        msg += "\n*🟡 Do sprawdzenia:*\n"
        for w in warnings:
            msg += f"• *{w['campaign']}* — {w['message']}\n"
    return msg


def _ads_pacing(client_key, client_cfg):
    now = datetime.now()
    days_elapsed = now.day - 1
    if days_elapsed < 1:
        return "⚠️ Pacing niedostępny — pierwszy dzień miesiąca."

    days_in_month  = calendar.monthrange(now.year, now.month)[1]
    days_remaining = days_in_month - now.day + 1
    first_of_month = now.replace(day=1).strftime('%Y-%m-%d')
    yesterday      = (now - timedelta(days=1)).strftime('%Y-%m-%d')

    campaigns_mtd = _fetch_ads_data(client_cfg, first_of_month, yesterday, min_spend=0)
    total_mtd  = sum(float(c.get("spend") or c.get("cost") or 0) for c in campaigns_mtd)
    daily_avg  = total_mtd / days_elapsed
    projected  = total_mtd + daily_avg * days_remaining
    pct_month  = (now.day - 1) / days_in_month * 100
    pct_budget = (total_mtd / projected * 100) if projected else 0
    pace_bar   = "🟢" if abs(pct_month - pct_budget) < 10 else ("🔴" if pct_budget < pct_month - 15 else "🟡")

    return (
        f"📊 *Pacing — {client_cfg['display_name']}* ({now.strftime('%B %Y')})\n"
        f"MTD: *{total_mtd:.0f} PLN* przez {days_elapsed} dni ({pct_month:.0f}% miesiąca)\n"
        f"Śr. dzienna: *{daily_avg:.0f} PLN/dzień*\n"
        f"Projekcja: {pace_bar} *{projected:.0f} PLN* end-of-month (zostało {days_remaining} dni)"
    )


def _ads_winners(client_key, client_cfg, days=7):
    date_to   = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    date_from = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    period_label = f"ostatnie {days}d ({date_from} — {date_to})"
    campaigns = _fetch_ads_data(client_cfg, date_from, date_to)
    if not campaigns:
        return f"⚠️ Brak danych ({period_label}) dla *{client_cfg['display_name']}*"

    tops = analyze_campaign_trends(campaigns, goal=client_cfg["goal"]).get("top_performers", [])
    if not tops:
        return f"🏆 *Winners — {client_cfg['display_name']}* ({period_label})\n_Brak wyraźnych liderów._"

    msg = f"🏆 *Winners — {client_cfg['display_name']}* ({period_label})\n"
    for i, t in enumerate(tops[:3], 1):
        msg += f"{i}. *{t['campaign']}*\n   {t.get('metrics_line', '')}\n"
    return msg


def _ads_losers(client_key, client_cfg, days=7):
    date_to   = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    date_from = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    period_label = f"ostatnie {days}d ({date_from} — {date_to})"
    campaigns = _fetch_ads_data(client_cfg, date_from, date_to)
    if not campaigns:
        return f"⚠️ Brak danych ({period_label}) dla *{client_cfg['display_name']}*"

    bm    = get_client_benchmarks(client_cfg["meta_name"], "meta", 30)
    bgoog = get_client_benchmarks(client_key, "google", 30)
    analysis = analyze_campaign_trends(campaigns, goal=client_cfg["goal"],
                                       meta_benchmarks=bm, google_benchmarks=bgoog)
    losers = analysis.get("critical_alerts", []) + analysis.get("warnings", [])

    if not losers:
        return f"💀 *Losers — {client_cfg['display_name']}* ({period_label})\n✅ Brak słabeuszy."

    msg = f"💀 *Losers — {client_cfg['display_name']}* ({period_label})\n"
    for l in losers[:3]:
        msg += f"• *{l['campaign']}* — {l['message']}\n"
        if l.get("action"):
            msg += f"  → {l['action']}\n"
    return msg


_ADS_SUBCOMMANDS = {
    "health":    _ads_health,
    "anomalies": _ads_anomalies,
    "anomalie":  _ads_anomalies,
    "pacing":    _ads_pacing,
    "winners":   _ads_winners,
    "losers":    _ads_losers,
}


def _dispatch_ads_command(subcmd, channel_id, extra_text, respond_fn):
    """Wspólna logika: rozwiązuje klienta i wywołuje właściwą funkcję."""
    fn = _ADS_SUBCOMMANDS.get(subcmd.lower())
    if not fn:
        known = " | ".join(f"`{k}`" for k in ["health", "anomalies", "pacing", "winners", "losers"])
        respond_fn(f"❓ Nieznana komenda: *{subcmd}*\nDostępne: {known}")
        return

    client_key, client_cfg = _resolve_ads_client(channel_id, extra_text)
    if not client_cfg:
        known_clients = ", ".join(f"`{k}`" for k in AD_CLIENTS)
        respond_fn(f"❓ Nie wiem jakiego klienta masz na myśli.\n"
                   f"Dostępni klienci: {known_clients}\n"
                   f"Przykład: `/ads health dre` lub `/ads health dre 14d`")
        return

    days = _parse_period(extra_text, default=7)
    try:
        sig = inspect.signature(fn)
        result = fn(client_key, client_cfg, days=days) if "days" in sig.parameters else fn(client_key, client_cfg)
        respond_fn(result)
    except Exception as _e:
        logger.error(f"Błąd ads cmd {subcmd}/{client_key}: {_e}")
        respond_fn(f"❌ Błąd podczas pobierania danych: {_e}")
