"""Daily digest + weekly learnings for DRE."""
import os
import json
import logging
from datetime import datetime, timedelta

import _ctx
from config.constants import CLIENT_GOALS, _DIGEST_LAST_SENT_FILE, _DIGEST_INTERVAL_DAYS
from tools.meta_ads import meta_ads_tool
from tools.google_ads import google_ads_tool
from jobs.performance_analysis import (
    get_client_benchmarks, analyze_campaign_trends, save_campaign_results,
    _detect_campaign_objective, _extract_engagement_actions,
    suggest_experiments, analyze_patterns, generate_smart_recommendations,
    _save_prediction, _confidence_label, _load_history_raw, load_campaign_history,
    calculate_confidence,
)

logger = logging.getLogger(__name__)


def _build_objective_alerts(meta_campaigns, google_campaigns=None):
    """Generuje alerty dostosowane do celu kampanii (per-campaign objective)."""
    alerts = []
    for c in (meta_campaigns or []):
        obj   = c.get('_objective', 'conversion')
        name  = c.get('campaign_name', '?')
        spend = float(c.get('spend', 0) or 0)
        freq  = c.get('frequency')

        if obj == 'engagement':
            eng = c.get('_engagement', {})
            total_interactions = (eng.get('reactions', 0) + eng.get('comments', 0) +
                                  eng.get('post_saves', 0) + eng.get('shares', 0))
            reach = int(c.get('reach', 0) or 0)
            if reach > 500 and total_interactions == 0:
                alerts.append({
                    'campaign': name,
                    'message': 'Zero interakcji przy aktywnym zasięgu — sprawdź kreacje',
                    'action': 'Zmień grafikę / copy lub odśwież grupę odbiorców',
                })
            if freq and freq >= 5.0:
                alerts.append({
                    'campaign': name,
                    'message': f'Frequency {freq:.1f} ≥ 5 — ryzyko ad fatigue',
                    'action': 'Wymień kreacje lub rozszerz grupę odbiorców',
                })

        elif obj == 'reach':
            if freq and freq >= 6.0:
                alerts.append({
                    'campaign': name,
                    'message': f'Frequency {freq:.1f} ≥ 6 — zbyt duże nasycenie',
                    'action': 'Rozszerz targeting lub zmień kreacje',
                })

        elif obj == 'traffic':
            ctr = float(c.get('ctr', 0) or 0)
            cpc = float(c.get('cpc', 0) or 0)
            if ctr < 0.5 and spend > 50:
                alerts.append({
                    'campaign': name,
                    'message': f'CTR {ctr:.2f}% < 0.5% — bardzo niska klikalność',
                    'action': 'Zmień kreację / CTA lub zawęź targeting',
                })
            if cpc > 4 and spend > 50:
                alerts.append({
                    'campaign': name,
                    'message': f'CPC {cpc:.2f} PLN > 4 PLN — wysoki koszt dla kampanii traffic',
                    'action': 'Przetestuj nowe kreacje lub zmień strategię bidowania',
                })

        else:  # conversion
            roas = c.get('purchase_roas')
            if roas is not None and roas < 1.5 and spend > 50:
                alerts.append({
                    'campaign': name,
                    'message': f'ROAS {roas:.2f}x < 1.5 — poniżej break-even',
                    'action': 'Pause lub głęboka optymalizacja targetingu/kreacji',
                })
            ctr = float(c.get('ctr', 0) or 0)
            if ctr < 0.5 and spend > 50:
                alerts.append({
                    'campaign': name,
                    'message': f'CTR {ctr:.2f}% < 0.5%',
                    'action': 'Zmień kreację lub targeting',
                })

    for c in (google_campaigns or []):
        name  = c.get('campaign_name', c.get('name', '?'))
        ctr   = float(c.get('ctr', 0) or 0)
        spend = float(c.get('cost', c.get('spend', 0)) or 0)
        if ctr < 1.0 and spend > 50:
            alerts.append({
                'campaign': f'{name} (Google)',
                'message': f'CTR {ctr:.2f}% < 1% — niska klikalność w wyszukiwarce',
                'action': 'Sprawdź treść reklam i słowa kluczowe',
            })
    return alerts


# ── WEEKLY LEARNINGS ───────────────────────────────────────────────────────────

def generate_weekly_learnings(client="dre"):
    """Weekly summary of predictions accuracy + learned patterns."""
    data = _load_history_raw()
    predictions = data.get(client, {}).get("predictions", [])
    cutoff = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    week_preds = [p for p in predictions if p.get("date", "") >= cutoff]

    patterns = analyze_patterns(client)
    summary  = patterns.get("summary", {})
    text = "🧠 **WEEKLY LEARNINGS – Co nauczyłem się w tym tygodniu:**\n\n"

    # Evaluate predictions
    if week_preds:
        all_hist = load_campaign_history(client, days_back=30)
        verified = []
        for pred in week_preds:
            camp_hist  = all_hist.get(pred["campaign"], [])
            after_date = (datetime.strptime(pred["date"], '%Y-%m-%d') + timedelta(days=2)).strftime('%Y-%m-%d')
            before = [e for e in camp_hist if e.get("date", "") < after_date]
            after  = [e for e in camp_hist if e.get("date", "") >= after_date]
            metric = pred.get("predicted_metric", "ctr")
            if before and after:
                bv = before[-1].get(metric)
                av = after[0].get(metric)
                if bv and av and bv > 0:
                    actual_chg = (av - bv) / bv * 100
                    pred_chg   = pred.get("predicted_change_pct", 0)
                    verified.append({**pred, "actual_change_pct": actual_chg,
                                     "success": (actual_chg > 0) == (pred_chg > 0)})

        if verified:
            for v in verified[:4]:
                icon = "✅" if v["success"] else "❌"
                text += f"{icon} **{v['campaign']}** – {v['recommendation']}\n"
                text += f"   Predicted: {v.get('predicted_change_pct', 0):+.0f}% | Actual: {v.get('actual_change_pct', 0):+.0f}%\n\n"
            acc = sum(1 for v in verified if v["success"]) / len(verified) * 100
            text += f"🎯 **Accuracy: {acc:.0f}%** ({len(verified)} predictions verified)\n\n"
            text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    freq_p = summary.get("frequency_creative", {})
    if freq_p and freq_p.get("total", 0) >= 2:
        text += (f"📌 **Creative refresh pattern** ({freq_p['total']} obserwacji):\n"
                 f"   {freq_p['successes']}/{freq_p['total']} razy pomogło"
                 f" | Avg CTR +{freq_p['avg_ctr_improvement_pct']:.0f}%\n\n")

    weekend = summary.get("weekend", {})
    if weekend:
        ctr_d  = weekend.get("ctr_diff_pct", 0)
        roas_d = weekend.get("roas_diff_pct", 0)
        we_cnt = len(patterns.get("weekend_we", []))
        text += (f"📌 **Weekend vs Weekday** ({we_cnt} weekend-dni):\n"
                 f"   CTR: {'🟢 +' if ctr_d > 0 else '🔴 '}{abs(ctr_d):.1f}% w weekendy\n"
                 f"   ROAS: {'🟢 +' if roas_d > 0 else '🔴 '}{abs(roas_d):.1f}% w weekendy\n\n")

    budget_p = summary.get("budget_increase", {})
    if budget_p and budget_p.get("total", 0) >= 2:
        text += (f"📌 **Budget increase pattern** ({budget_p['total']} obserwacji):\n"
                 f"   {budget_p['successes']}/{budget_p['total']} razy CPC nie wzrósł >10%\n\n")

    if not freq_p and not weekend and not week_preds:
        text += ("ℹ️ Za mało danych historycznych – bot zbiera dane od dziś.\n"
                 "Po 2-3 tygodniach zacznę wykrywać wzorce.\n")
    return text


def weekly_learnings_dre():
    """Wysyła weekly learnings w poniedziałek i czwartek 8:30."""
    try:
        dre_channel = os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")
        logger.info("🧠 Generuję Weekly Learnings DRE...")
        text = generate_weekly_learnings("dre")
        _ctx.app.client.chat_postMessage(channel=dre_channel, text=text)
        logger.info("✅ Weekly Learnings wysłane!")
    except Exception as e:
        logger.error(f"❌ Błąd weekly_learnings_dre: {e}")


# ── DAILY DIGEST ───────────────────────────────────────────────────────────────

def generate_daily_digest_dre():
    """Generuje daily digest dla klienta DRE (Meta + Google Ads) z benchmarkami."""
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        today     = datetime.now().strftime('%Y-%m-%d')

        meta_benchmarks   = get_client_benchmarks("drzwi dre", "meta", lookback_days=30)
        google_benchmarks = get_client_benchmarks("dre", "google", lookback_days=30)

        client_goal = CLIENT_GOALS.get("drzwi dre", "conversion")

        # === META ADS ===
        meta_data = meta_ads_tool(
            client_name="drzwi dre",
            date_from=yesterday, date_to=today,
            level="campaign",
            metrics=["campaign_name", "spend", "impressions", "clicks", "ctr", "cpc",
                     "reach", "frequency", "conversions", "purchase_roas", "actions"]
        )

        # === GOOGLE ADS ===
        google_data_combined = []
        for account in ["dre", "dre 2024", "dre 2025"]:
            data = google_ads_tool(
                client_name=account,
                date_from=yesterday, date_to=today,
                level="campaign",
                metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                         "metrics.cost_micros", "metrics.conversions", "metrics.ctr",
                         "metrics.average_cpc"]
            )
            if data.get("data"):
                google_data_combined.extend(data["data"])

        meta_campaigns_raw  = meta_data.get("data", [])
        all_campaigns_raw   = meta_campaigns_raw + google_data_combined

        if not all_campaigns_raw:
            return "📊 DRE - Daily Digest\n\n⚠️ Brak danych za wczoraj. Sprawdź czy kampanie są aktywne."

        MIN_SPEND_PLN = 20.0
        meta_campaigns       = [c for c in meta_campaigns_raw
                                 if float(c.get("spend", 0) or 0) >= MIN_SPEND_PLN]
        google_data_combined = [c for c in google_data_combined
                                 if float(c.get("cost", c.get("spend", 0)) or 0) >= MIN_SPEND_PLN]
        all_campaigns = meta_campaigns + google_data_combined
        skipped_count = len(all_campaigns_raw) - len(all_campaigns)

        if not all_campaigns:
            return "📊 DRE - Daily Digest\n\n⚠️ Brak kampanii z spendem ≥ 20 PLN za wczoraj."

        # Annotacja celu per kampania (Meta)
        for c in meta_campaigns:
            c['_objective'] = _detect_campaign_objective(c.get('campaign_name', ''))
            c['_engagement'] = _extract_engagement_actions(c)

        # Zapisz do historii
        for c in meta_campaigns_raw:
            name = c.get("campaign_name", "")
            if name:
                save_campaign_results("dre", name, {
                    "ctr": c.get("ctr"), "cpc": c.get("cpc"),
                    "roas": c.get("purchase_roas"), "frequency": c.get("frequency"),
                    "spend": c.get("spend", 0), "conversions": c.get("conversions", 0),
                    "impressions": c.get("impressions", 0), "clicks": c.get("clicks", 0),
                    "platform": "meta",
                })
        google_data_raw = [c for c in all_campaigns_raw if c not in meta_campaigns_raw]
        for c in google_data_raw:
            name = c.get("campaign_name", c.get("name", ""))
            if name:
                save_campaign_results("dre", name, {
                    "ctr": c.get("ctr"), "cpc": c.get("cpc"), "roas": None,
                    "spend": c.get("cost", c.get("spend", 0)),
                    "conversions": c.get("conversions", 0),
                    "impressions": c.get("impressions", 0), "clicks": c.get("clicks", 0),
                    "platform": "google",
                })

        analysis = analyze_campaign_trends(
            all_campaigns, goal=client_goal,
            meta_benchmarks=meta_benchmarks, google_benchmarks=google_benchmarks,
        )

        total_spend       = sum(c.get("spend", 0) or c.get("cost", 0) for c in all_campaigns)
        total_clicks      = sum(c.get("clicks", 0) for c in all_campaigns)
        total_impressions = sum(c.get("impressions", 0) for c in all_campaigns)
        total_reach       = sum(c.get("reach", 0) for c in all_campaigns)

        # TL;DR
        obj_alerts  = _build_objective_alerts(meta_campaigns, google_data_combined)
        n_alerts    = len(obj_alerts)
        alert_note  = f" | 🔴 {n_alerts} alert{'y' if n_alerts > 1 else ''}" if n_alerts else " | ✅ bez alertów"
        skipped_note = f" (+{skipped_count} <20PLN)" if skipped_count > 0 else ""

        digest = (
            f"📊 *DRE {yesterday}* | "
            f"💰 {total_spend:.0f} PLN | "
            f"📈 {len(all_campaigns)} kampanii{skipped_note}"
            f"{alert_note}\n"
        )

        # Kampanie — metryki per cel
        digest += "\n*📋 Kampanie:*\n"
        for c in meta_campaigns:
            obj   = c.get('_objective', 'conversion')
            name  = c.get('campaign_name', '?')
            spend = float(c.get('spend', 0) or 0)
            reach = int(c.get('reach', 0) or 0)
            freq  = c.get('frequency')
            eng   = c.get('_engagement', {})

            if obj == 'engagement':
                reactions = eng.get('reactions', 0)
                comments  = eng.get('comments', 0)
                saves     = eng.get('post_saves', 0)
                shares    = eng.get('shares', 0)
                digest += (
                    f"🎯 *[ENGAGEMENT]* {name}\n"
                    f"   ❤️ Reakcje: {reactions} | 💬 Komentarze: {comments} | "
                    f"🔖 Zapisy: {saves} | 🔁 Udostępnienia: {shares}\n"
                    f"   💰 Spend: {spend:.0f} PLN"
                    + (f" | 👥 Zasięg: {reach:,}" if reach else "")
                    + (f" | 📊 Freq: {freq:.1f}" if freq else "")
                    + "\n"
                )
            elif obj == 'reach':
                cpm = float(c.get('cpm', 0) or 0)
                digest += (
                    f"📡 *[REACH]* {name}\n"
                    f"   👥 Zasięg: {reach:,} | 📊 Freq: {f'{freq:.1f}' if freq else '—'} | 💰 {spend:.0f} PLN"
                    + (f" | CPM: {cpm:.2f} PLN" if cpm else "")
                    + "\n"
                )
            elif obj == 'traffic':
                clicks = int(c.get('clicks', 0) or 0)
                ctr    = float(c.get('ctr', 0) or 0)
                cpc    = float(c.get('cpc', 0) or 0)
                digest += (
                    f"🔗 *[TRAFFIC]* {name}\n"
                    f"   👆 Kliknięcia: {clicks} | CTR: {ctr:.2f}% | CPC: {cpc:.2f} PLN | 💰 {spend:.0f} PLN\n"
                )
            else:  # conversion
                roas  = c.get('purchase_roas')
                convs = int(c.get('conversions', 0) or 0)
                ctr   = float(c.get('ctr', 0) or 0)
                digest += (
                    f"🛒 *[CONVERSION]* {name}\n"
                    f"   🎯 ROAS: {f'{roas:.2f}x' if roas else '—'} | "
                    f"🔄 Konwersje: {convs} | CTR: {ctr:.2f}% | 💰 {spend:.0f} PLN\n"
                )

        for c in google_data_combined:
            name  = c.get('campaign_name', c.get('name', '?'))
            spend = float(c.get('cost', c.get('spend', 0)) or 0)
            ctr   = float(c.get('ctr', 0) or 0)
            convs = int(c.get('conversions', 0) or 0)
            digest += (
                f"🔍 *[GOOGLE]* {name}\n"
                f"   🔄 Konwersje: {convs} | CTR: {ctr:.2f}% | 💰 {spend:.0f} PLN\n"
            )

        # Akcja wymagana
        if obj_alerts:
            digest += "\n*🔴 AKCJA WYMAGANA:*\n"
            for alert in obj_alerts:
                digest += f"• *{alert['campaign']}* — {alert['message']}\n"
                if alert.get("action"):
                    digest += f"  → {alert['action']}\n"

        # Eksperyment tygodnia
        try:
            experiments = suggest_experiments("dre", all_campaigns)
            if experiments:
                exp = experiments[0]
                digest += (
                    f"\n*🧪 EKSPERYMENT:* {exp['experiment']}\n"
                    f"  _{exp.get('reason', '')} | expected: {exp.get('expected', '')}_\n"
                )
        except Exception as _e:
            logger.error(f"Błąd suggest_experiments w digest: {_e}")

        # Zapisz predykcje w tle
        try:
            patterns = analyze_patterns("dre")
            recs = generate_smart_recommendations("dre", all_campaigns, patterns)
            for rec in recs[:4]:
                if _confidence_label(rec["confidence"]):
                    _save_prediction(
                        "dre", rec["campaign"], rec["action"],
                        rec.get("predicted_metric", "ctr"),
                        rec.get("predicted_change_pct", 20.0),
                        rec["confidence"],
                    )
        except Exception as _e:
            logger.error(f"Błąd predictions w digest: {_e}")

        return digest

    except Exception as e:
        logger.error(f"Błąd generowania digestu: {e}")
        return f"❌ Błąd generowania digestu: {str(e)}"


def daily_digest_dre():
    """Wysyła daily digest dla DRE co 3 dni (cron codziennie o 9:00, ale z guard)."""
    try:
        if os.path.exists(_DIGEST_LAST_SENT_FILE):
            with open(_DIGEST_LAST_SENT_FILE, 'r', encoding='utf-8') as _f:
                _last = json.load(_f).get('date', '')
            if _last:
                _days_ago = (datetime.now() - datetime.strptime(_last, '%Y-%m-%d')).days
                if _days_ago < _DIGEST_INTERVAL_DAYS:
                    logger.info(f"Digest DRE skip — wysłany {_last} ({_days_ago}d temu)")
                    return
    except Exception as _e:
        logger.warning(f"Digest guard error (ignoruję): {_e}")

    try:
        dre_channel_id = os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")
        logger.info("🔥 Generuję Daily Digest dla DRE...")
        digest = generate_daily_digest_dre()
        _ctx.app.client.chat_postMessage(channel=dre_channel_id, text=digest)
        logger.info("✅ Daily Digest wysłany!")

        try:
            os.makedirs(os.path.dirname(_DIGEST_LAST_SENT_FILE), exist_ok=True)
            with open(_DIGEST_LAST_SENT_FILE, 'w', encoding='utf-8') as _f:
                json.dump({'date': datetime.now().strftime('%Y-%m-%d')}, _f)
        except Exception as _e:
            logger.warning(f"Nie udało się zapisać digest_last_sent: {_e}")

    except Exception as e:
        logger.error(f"❌ Błąd wysyłania digestu: {e}")
