"""Daily digest + weekly learnings for DRE."""
import os
import logging
from datetime import datetime, timedelta

import _ctx
from config.constants import CLIENT_GOALS, _DIGEST_INTERVAL_DAYS
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

# CTR benchmarks per Google campaign type:
#   search:  1% threshold (search ads)
#   display: 0.1% threshold (GDN — display impressions)
#   video:   skip CTR alert (YouTube — view-rate metric)
#   pmax/shopping/unknown: skip CTR alert (conversions are the KPI)

def _google_campaign_type(name: str) -> str:
    """Detect Google campaign type from name tags like [gdn], [search], [yt]."""
    n = name.lower()
    if '[search]' in n or '[srch]' in n:
        return 'search'
    if '[gdn]' in n or '[display]' in n:
        return 'display'
    if '[yt]' in n or '[youtube]' in n or '[video]' in n:
        return 'video'
    if '[pmax]' in n or 'performance max' in n:
        return 'pmax'
    if '[shopping]' in n:
        return 'shopping'
    return 'search'  # default


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
        name      = c.get('campaign_name', c.get('name', '?'))
        ctr       = float(c.get('ctr', 0) or 0)
        spend     = float(c.get('cost', c.get('spend', 0)) or 0)
        camp_type = _google_campaign_type(name)
        if camp_type == 'search':
            if ctr < 1.0 and spend > 50:
                alerts.append({
                    'campaign': f'{name} (Google)',
                    'message': f'CTR {ctr:.2f}% < 1% — niska klikalność w wyszukiwarce',
                    'action': 'Sprawdź treść reklam i słowa kluczowe',
                })
        elif camp_type == 'display':
            if ctr < 0.1 and spend > 50:
                alerts.append({
                    'campaign': f'{name} (Google)',
                    'message': f'CTR {ctr:.2f}% < 0.1% — niska klikalność (Display/GDN)',
                    'action': 'Sprawdź kreacje banerowe i targetowanie',
                })
        # video/pmax/shopping: CTR nie jest główną metryką — brak alertu CTR
    return alerts


def _classify_campaigns(meta_campaigns, google_campaigns):
    """Klasyfikuje kampanie na: best, watch, poor."""
    best, watch, poor = [], [], []

    for c in meta_campaigns:
        obj   = c.get('_objective', 'conversion')
        name  = c.get('campaign_name', '?')
        spend = float(c.get('spend', 0) or 0)
        ctr   = float(c.get('ctr', 0) or 0)
        eng   = c.get('_engagement', {})

        if obj == 'engagement':
            total_eng = (eng.get('reactions', 0) + eng.get('comments', 0) +
                         eng.get('post_saves', 0) + eng.get('shares', 0))
            reach = int(c.get('reach', 0) or 0)
            eng_rate = (total_eng / reach * 100) if reach > 0 else 0
            if eng_rate >= 3:
                best.append((name, f'Engagement rate {eng_rate:.1f}% — świetne zaangażowanie'))
            elif total_eng == 0 and reach > 500:
                poor.append((name, 'Zero interakcji — kreacje nie działają'))
            else:
                watch.append((name, f'{total_eng} interakcji — monitoruj'))

        elif obj == 'reach':
            freq = c.get('frequency') or 0
            reach = int(c.get('reach', 0) or 0)
            if freq < 3 and reach > 1000:
                best.append((name, f'Dobry zasięg {reach:,} przy niskiej frequency {freq:.1f}'))
            elif freq >= 6:
                poor.append((name, f'Ad fatigue — frequency {freq:.1f} ≥ 6'))
            else:
                watch.append((name, f'Zasięg {reach:,} | freq {freq:.1f}'))

        elif obj == 'traffic':
            cpc = float(c.get('cpc', 0) or 0)
            if ctr >= 1.5:
                best.append((name, f'Wysoki CTR {ctr:.2f}% — kreacja działa'))
            elif ctr < 0.5 and spend > 50:
                poor.append((name, f'CTR {ctr:.2f}% — bardzo niska klikalność'))
            else:
                watch.append((name, f'CTR {ctr:.2f}% — przeciętny wynik'))

        else:  # conversion
            roas = c.get('purchase_roas')
            convs = int(c.get('conversions', 0) or 0)
            if roas and roas >= 3:
                best.append((name, f'ROAS {roas:.2f}x — bardzo dobry wynik'))
            elif convs == 0 and spend > 50:
                poor.append((name, 'Zero konwersji przy aktywnym budżecie'))
            elif roas and roas < 1.5:
                poor.append((name, f'ROAS {roas:.2f}x — poniżej break-even'))
            else:
                watch.append((name, f'ROAS {f"{roas:.2f}x" if roas else "brak"} | {convs} konwersji'))

    for c in google_campaigns:
        name      = c.get('campaign_name', c.get('name', '?'))
        ctr       = float(c.get('ctr', 0) or 0)
        convs     = int(c.get('conversions', 0) or 0)
        spend     = float(c.get('cost', c.get('spend', 0)) or 0)
        camp_type = _google_campaign_type(name)
        label     = f'{name} (Google)'

        if camp_type == 'search':
            if ctr >= 3 and convs > 0:
                best.append((label, f'CTR {ctr:.2f}% + {convs} konwersji'))
            elif ctr < 1 and spend > 50:
                poor.append((label, f'CTR {ctr:.2f}% — niska klikalność Search'))
            else:
                watch.append((label, f'CTR {ctr:.2f}% | {convs} konwersji'))
        elif camp_type == 'display':
            if convs > 0 and ctr >= 0.1:
                best.append((label, f'CTR {ctr:.2f}% + {convs} konwersji (Display)'))
            elif ctr < 0.05 and spend > 50:
                poor.append((label, f'CTR {ctr:.2f}% — niska klikalność (GDN)'))
            else:
                watch.append((label, f'CTR {ctr:.2f}% | {convs} konwersji (Display)'))
        elif camp_type == 'video':
            if convs > 0:
                best.append((label, f'{convs} konwersji (YouTube)'))
            else:
                watch.append((label, f'CTR {ctr:.2f}% | {convs} konwersji (YouTube)'))
        else:  # pmax, shopping, unknown
            if convs > 0:
                best.append((label, f'{convs} konwersji'))
            else:
                watch.append((label, f'CTR {ctr:.2f}% | {convs} konwersji'))

    return best, watch, poor


def _build_main_message(date_label, total_spend, total_reach, avg_ctr,
                         total_conversions, campaign_count, obj_alerts, skipped_count,
                         meta_count=0, google_count=0,
                         meta_spend=0, meta_reach=0, meta_ctr=0, meta_conversions=0,
                         google_spend=0, google_ctr=0, google_conversions=0):
    """Buduje krótką wiadomość główną (widoczna na kanale)."""
    skipped_note = f" _(+{skipped_count} poniżej 20 PLN pominięto)_" if skipped_count > 0 else ""
    both = meta_count > 0 and google_count > 0

    lines = [
        f"📊 *META + GOOGLE ADS – DRE | {date_label}*{skipped_note}" if both
        else f"📊 *{'META ADS' if meta_count > 0 else 'GOOGLE ADS'} – DRE | {date_label}*{skipped_note}",
        "",
    ]

    if both:
        lines += [
            f"🔵 *META ADS* — {meta_count} kampanii",
            f"   💰 Spend: *{meta_spend:.0f} PLN* | 👥 Reach: *{meta_reach:,}* | 📈 CTR: *{meta_ctr:.2f}%* | 🎯 Konwersje: *{meta_conversions}*",
            "",
            f"🔴 *GOOGLE ADS* — {google_count} kampanii",
            f"   💰 Spend: *{google_spend:.0f} PLN* | 📈 Avg CTR: *{google_ctr:.2f}%* | 🎯 Konwersje: *{google_conversions}*",
            "",
            f"📣 Łącznie aktywnych kampanii: *{campaign_count}*",
        ]
    else:
        lines += [
            f"💰 Spend: *{total_spend:.0f} PLN*",
            f"👥 Reach: *{total_reach:,}*",
            f"📈 Avg CTR: *{avg_ctr:.2f}%*",
            f"🎯 Konwersje: *{total_conversions}*",
            f"📣 Kampanie aktywne: *{campaign_count}*",
        ]

    # Alerty pogrupowane per platforma
    meta_alerts   = [a for a in obj_alerts if '(Google)' not in a['campaign']]
    google_alerts = [a for a in obj_alerts if '(Google)' in a['campaign']]

    if obj_alerts:
        lines.append("")
        lines.append("⚠️ *ALERTY*")
        if both and meta_alerts:
            lines.append("_Meta:_")
            for alert in meta_alerts:
                lines.append(f"• *{alert['campaign']}* — {alert['message']}")
        if both and google_alerts:
            lines.append("_Google:_")
            for alert in google_alerts:
                lines.append(f"• *{alert['campaign']}* — {alert['message']}")
        if not both:
            for alert in obj_alerts:
                lines.append(f"• *{alert['campaign']}* — {alert['message']}")
    else:
        lines.append("")
        lines.append("✅ Brak alertów — wszystko wygląda OK")

    lines.append("")
    lines.append("💬 _Szczegółowa analiza kampanii w threadzie ↓_")

    return "\n".join(lines)


def _build_thread_message(meta_campaigns, google_campaigns, obj_alerts, experiments, smart_recs):
    """Buduje szczegółową analizę do threadu."""
    best, watch, poor = _classify_campaigns(meta_campaigns, google_campaigns)
    lines = []

    # ── SZYBKIE WNIOSKI ────────────────────────────────────────────────────────
    lines.append("🧠 *SZYBKIE WNIOSKI*")
    lines.append("")

    if best:
        lines.append("🟢 *Najlepsze kampanie*")
        for name, reason in best:
            lines.append(f"• {name} — {reason}")
        lines.append("")

    if watch:
        lines.append("🟡 *Do obserwacji*")
        for name, reason in watch:
            lines.append(f"• {name} — {reason}")
        lines.append("")

    if poor:
        lines.append("🔴 *Słabe wyniki*")
        for name, reason in poor:
            lines.append(f"• {name} — {reason}")
        lines.append("")

    # ── KAMPANIE WG CELU ───────────────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("📊 *KAMPANIE WG CELU*")
    lines.append("")

    # Pogrupuj Meta kampanie wg celu
    by_obj = {}
    for c in meta_campaigns:
        obj = c.get('_objective', 'conversion').upper()
        by_obj.setdefault(obj, []).append(c)

    obj_order  = ['CONVERSION', 'ENGAGEMENT', 'REACH', 'TRAFFIC']
    obj_labels = {
        'CONVERSION': '🛒 *CONVERSION*',
        'ENGAGEMENT': '❤️ *ENGAGEMENT*',
        'REACH':      '📡 *REACH*',
        'TRAFFIC':    '🔗 *TRAFFIC*',
    }

    for obj_key in obj_order:
        campaigns_in_obj = by_obj.get(obj_key, [])
        if not campaigns_in_obj:
            continue
        lines.append(obj_labels[obj_key])
        lines.append("")

        for c in campaigns_in_obj:
            name  = c.get('campaign_name', '?')
            spend = float(c.get('spend', 0) or 0)
            ctr   = float(c.get('ctr', 0) or 0)
            reach = int(c.get('reach', 0) or 0)
            freq  = c.get('frequency')
            eng   = c.get('_engagement', {})

            lines.append(f"*{name}*")
            lines.append(f"💰 Spend: {spend:.0f} PLN")

            if obj_key == 'ENGAGEMENT':
                reactions = eng.get('reactions', 0)
                comments  = eng.get('comments', 0)
                saves     = eng.get('post_saves', 0)
                shares    = eng.get('shares', 0)
                total_eng = reactions + comments + saves + shares
                lines.append(f"❤️ Reakcje: {reactions} | 💬 Komentarze: {comments} | 🔖 Zapisy: {saves} | 🔁 Udostępnienia: {shares}")
                lines.append(f"📈 CTR: {ctr:.2f}%")
                lines.append(f"👥 Reach: {reach:,}" + (f" | 🔁 Frequency: {freq:.1f}" if freq else ""))
                eng_rate = (total_eng / reach * 100) if reach > 0 else 0
                insight = (
                    "Dobre zaangażowanie — kontynuuj." if eng_rate >= 3
                    else "Zero interakcji — sprawdź kreacje i grupę odbiorców." if total_eng == 0 and reach > 500
                    else "Zaangażowanie przeciętne — rozważ odświeżenie kreacji."
                )

            elif obj_key == 'REACH':
                cpm = float(c.get('cpm', 0) or 0)
                lines.append(f"👥 Reach: {reach:,}" + (f" | 🔁 Frequency: {freq:.1f}" if freq else ""))
                if cpm:
                    lines.append(f"📊 CPM: {cpm:.2f} PLN")
                insight = (
                    f"Ad fatigue — frequency {freq:.1f} ≥ 6, wymień kreacje." if freq and freq >= 6
                    else "Dobry zasięg przy zdrowej frequency." if reach > 1000
                    else "Zasięg niski — rozważ poszerzenie targetowania."
                )

            elif obj_key == 'TRAFFIC':
                clicks = int(c.get('clicks', 0) or 0)
                cpc    = float(c.get('cpc', 0) or 0)
                lines.append(f"📈 CTR: {ctr:.2f}% | 👆 Kliknięcia: {clicks} | 💸 CPC: {cpc:.2f} PLN")
                lines.append(f"👥 Reach: {reach:,}" + (f" | 🔁 Frequency: {freq:.1f}" if freq else ""))
                insight = (
                    f"Wysoki CTR {ctr:.2f}% — kreacja działa." if ctr >= 1.5
                    else f"CTR {ctr:.2f}% — niska klikalność, zmień kreację lub CTA." if ctr < 0.5 and spend > 50
                    else f"CTR {ctr:.2f}% — wynik przeciętny, testuj nowe warianty."
                )

            else:  # CONVERSION
                roas  = c.get('purchase_roas')
                convs = int(c.get('conversions', 0) or 0)
                lines.append(f"📈 CTR: {ctr:.2f}% | 🎯 Konwersje: {convs} | 💸 ROAS: {f'{roas:.2f}x' if roas else '—'}")
                lines.append(f"👥 Reach: {reach:,}" + (f" | 🔁 Frequency: {freq:.1f}" if freq else ""))
                insight = (
                    f"ROAS {roas:.2f}x — bardzo dobry wynik." if roas and roas >= 3
                    else "Dobry CTR, ale brak konwersji — możliwy problem z landing page lub pixel trackingiem." if ctr > 1 and convs == 0
                    else "Zero konwersji i niski spend — daj kampanii więcej czasu." if convs == 0 and spend <= 100
                    else f"Zero konwersji przy budżecie {spend:.0f} PLN — zatrzymaj lub zoptymalizuj." if convs == 0
                    else f"ROAS {roas:.2f}x poniżej break-even — optymalizuj lub pausuj." if roas and roas < 1.5
                    else f"{convs} konwersje — monitoruj trend."
                )

            lines.append(f"💡 _Insight: {insight}_")
            lines.append("")

    # Google
    if google_campaigns:
        lines.append("🔍 *GOOGLE ADS*")
        lines.append("")
        for c in google_campaigns:
            name      = c.get('campaign_name', c.get('name', '?'))
            spend     = float(c.get('cost', c.get('spend', 0)) or 0)
            ctr       = float(c.get('ctr', 0) or 0)
            convs     = int(c.get('conversions', 0) or 0)
            camp_type = _google_campaign_type(name)
            lines.append(f"*{name}*")
            lines.append(f"💰 Spend: {spend:.0f} PLN | 📈 CTR: {ctr:.2f}% | 🎯 Konwersje: {convs}")
            if camp_type == 'search':
                insight = (
                    f"CTR {ctr:.2f}% + {convs} konwersji — świetny wynik." if ctr >= 3 and convs > 0
                    else f"CTR {ctr:.2f}% — niska klikalność, sprawdź reklamy i słowa kluczowe." if ctr < 1 and spend > 50
                    else f"CTR {ctr:.2f}% — wynik przeciętny."
                )
            elif camp_type == 'display':
                insight = (
                    f"CTR {ctr:.2f}% + {convs} konwersji — dobry wynik dla GDN." if convs > 0
                    else f"CTR {ctr:.2f}% — niska klikalność dla Display, sprawdź kreacje." if ctr < 0.05 and spend > 50
                    else f"CTR {ctr:.2f}% — typowy wynik dla GDN. Monitoruj konwersje."
                )
            elif camp_type == 'video':
                insight = (
                    f"CTR {ctr:.2f}% + {convs} konwersji — dobry wynik dla YouTube." if convs > 0
                    else f"CTR {ctr:.2f}% — CTR w YouTube nie jest kluczową metryką, sprawdź view-through."
                )
            else:
                insight = (
                    f"CTR {ctr:.2f}% + {convs} konwersji — dobry wynik." if convs > 0
                    else f"CTR {ctr:.2f}% — brak konwersji, monitoruj."
                )
            lines.append(f"💡 _Insight: {insight}_")
            lines.append("")

    # ── REKOMENDACJE AI ────────────────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("🚨 *REKOMENDACJE AI*")
    lines.append("")

    recs_added = 0

    # Alert-based rekomendacje
    for alert in obj_alerts:
        if alert.get('action'):
            lines.append(f"• *{alert['campaign']}:* {alert['action']}")
            recs_added += 1

    # Smart recommendations z historii
    for rec in (smart_recs or [])[:4]:
        action = rec.get('action', '')
        camp   = rec.get('campaign', '')
        conf   = rec.get('confidence', 0)
        if action and camp and conf >= 0.5:
            conf_label = _confidence_label(conf)
            lines.append(f"• *{camp}:* {action} _{f'({conf_label})' if conf_label else ''}_")
            recs_added += 1

    # Eksperyment tygodnia
    if experiments:
        exp = experiments[0]
        lines.append(f"• 🧪 *Eksperyment:* {exp['experiment']} — _{exp.get('reason', '')}_")
        recs_added += 1

    if recs_added == 0:
        lines.append("• Brak konkretnych rekomendacji — kampanie działają stabilnie.")

    return "\n".join(lines)


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
    """Generuje daily digest dla klienta DRE (Meta + Google Ads) — ostatnie 7 dni.

    Zwraca tuple (main_message, thread_message).
    """
    try:
        yesterday  = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        week_ago   = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        date_label = yesterday

        meta_benchmarks   = get_client_benchmarks("drzwi dre", "meta", lookback_days=30)
        google_benchmarks = get_client_benchmarks("dre", "google", lookback_days=30)

        client_goal = CLIENT_GOALS.get("drzwi dre", "conversion")

        # === META ADS (ostatnie 7 dni) ===
        meta_data = meta_ads_tool(
            client_name="drzwi dre",
            date_from=week_ago, date_to=yesterday,
            level="campaign",
            metrics=["campaign_name", "spend", "impressions", "clicks", "ctr", "cpc",
                     "reach", "frequency", "conversions", "purchase_roas", "actions"]
        )

        # === GOOGLE ADS (ostatnie 7 dni) ===
        google_data_combined = []
        for account in ["dre", "dre 2024", "dre 2025"]:
            data = google_ads_tool(
                client_name=account,
                date_from=week_ago, date_to=yesterday,
                level="campaign",
                metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                         "metrics.cost_micros", "metrics.conversions", "metrics.ctr",
                         "metrics.average_cpc"]
            )
            if data.get("data"):
                google_data_combined.extend(data["data"])

        meta_campaigns_raw  = meta_data.get("data", [])
        all_campaigns_raw   = meta_campaigns_raw + google_data_combined

        error_main = f"📊 *META ADS – DRE | {date_label}*\n\n⚠️ Brak danych za ostatnie 7 dni. Sprawdź czy kampanie są aktywne."
        if not all_campaigns_raw:
            return error_main, None

        MIN_SPEND_PLN = 20.0
        meta_campaigns       = [c for c in meta_campaigns_raw
                                 if float(c.get("spend", 0) or 0) >= MIN_SPEND_PLN]
        google_data_combined = [c for c in google_data_combined
                                 if float(c.get("cost", c.get("spend", 0)) or 0) >= MIN_SPEND_PLN]
        all_campaigns = meta_campaigns + google_data_combined
        skipped_count = len(all_campaigns_raw) - len(all_campaigns)

        if not all_campaigns:
            return (
                f"📊 *META ADS – DRE | {date_label}*\n\n"
                f"⚠️ Brak kampanii z spendem ≥ 20 PLN za ostatnie 7 dni.",
                None
            )

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

        analyze_campaign_trends(
            all_campaigns, goal=client_goal,
            meta_benchmarks=meta_benchmarks, google_benchmarks=google_benchmarks,
        )

        # ── Agregaty KPI ──────────────────────────────────────────────────────
        total_spend       = sum(float(c.get("spend", 0) or c.get("cost", 0) or 0) for c in all_campaigns)
        total_reach       = sum(int(c.get("reach", 0) or 0) for c in all_campaigns)
        total_conversions = sum(int(c.get("conversions", 0) or 0) for c in all_campaigns)

        ctrs = [float(c.get("ctr", 0) or 0) for c in all_campaigns if float(c.get("ctr", 0) or 0) > 0]
        avg_ctr = sum(ctrs) / len(ctrs) if ctrs else 0.0

        # Osobne statystyki per platforma
        meta_spend       = sum(float(c.get("spend", 0) or 0) for c in meta_campaigns)
        meta_reach       = sum(int(c.get("reach", 0) or 0) for c in meta_campaigns)
        meta_conversions = sum(int(c.get("conversions", 0) or 0) for c in meta_campaigns)
        meta_ctrs        = [float(c.get("ctr", 0) or 0) for c in meta_campaigns if float(c.get("ctr", 0) or 0) > 0]
        meta_ctr         = sum(meta_ctrs) / len(meta_ctrs) if meta_ctrs else 0.0

        google_spend       = sum(float(c.get("cost", c.get("spend", 0)) or 0) for c in google_data_combined)
        google_conversions = sum(int(c.get("conversions", 0) or 0) for c in google_data_combined)
        google_ctrs        = [float(c.get("ctr", 0) or 0) for c in google_data_combined if float(c.get("ctr", 0) or 0) > 0]
        google_ctr         = sum(google_ctrs) / len(google_ctrs) if google_ctrs else 0.0

        obj_alerts = _build_objective_alerts(meta_campaigns, google_data_combined)

        # ── Eksperymenty + smart recs (dla threadu) ───────────────────────────
        experiments = []
        smart_recs  = []
        try:
            experiments = suggest_experiments("dre", all_campaigns)
        except Exception as _e:
            logger.error(f"Błąd suggest_experiments w digest: {_e}")
        try:
            patterns   = analyze_patterns("dre")
            smart_recs = generate_smart_recommendations("dre", all_campaigns, patterns)
            for rec in smart_recs[:4]:
                if _confidence_label(rec["confidence"]):
                    _save_prediction(
                        "dre", rec["campaign"], rec["action"],
                        rec.get("predicted_metric", "ctr"),
                        rec.get("predicted_change_pct", 20.0),
                        rec["confidence"],
                    )
        except Exception as _e:
            logger.error(f"Błąd predictions w digest: {_e}")

        # ── Buduj wiadomości ──────────────────────────────────────────────────
        main_msg = _build_main_message(
            date_label=date_label,
            total_spend=total_spend,
            total_reach=total_reach,
            avg_ctr=avg_ctr,
            total_conversions=total_conversions,
            campaign_count=len(all_campaigns),
            obj_alerts=obj_alerts,
            skipped_count=skipped_count,
            meta_count=len(meta_campaigns),
            google_count=len(google_data_combined),
            meta_spend=meta_spend,
            meta_reach=meta_reach,
            meta_ctr=meta_ctr,
            meta_conversions=meta_conversions,
            google_spend=google_spend,
            google_ctr=google_ctr,
            google_conversions=google_conversions,
        )

        thread_msg = _build_thread_message(
            meta_campaigns=meta_campaigns,
            google_campaigns=google_data_combined,
            obj_alerts=obj_alerts,
            experiments=experiments,
            smart_recs=smart_recs,
        )

        return main_msg, thread_msg

    except Exception as e:
        logger.error(f"Błąd generowania digestu: {e}")
        return f"❌ Błąd generowania digestu: {str(e)}", None


def _digest_days_since_last_sent(channel_id: str) -> int:
    """Sprawdza w historii Slacka ile dni minęło od ostatniego digestu.
    Zwraca 999 jeśli nigdy nie wysłano lub błąd (bezpieczny fallback = wyślij)."""
    try:
        history = _ctx.app.client.conversations_history(channel=channel_id, limit=100)
        for msg in history.get("messages", []):
            if not msg.get("bot_id"):
                continue
            text = msg.get("text", "")
            if "DRE" in text and ("META ADS" in text or "📊 *META ADS" in text):
                msg_ts   = float(msg["ts"])
                days_ago = (datetime.now() - datetime.fromtimestamp(msg_ts)).days
                logger.info(f"Ostatni digest DRE: {days_ago}d temu (ts={msg['ts']})")
                return days_ago
    except Exception as _e:
        logger.warning(f"_digest_days_since_last_sent error: {_e}")
    return 999


def daily_digest_dre():
    """Wysyła digest dla DRE co _DIGEST_INTERVAL_DAYS dni. Guard oparty o historię Slacka."""
    try:
        dre_channel_id = os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")
        days_ago = _digest_days_since_last_sent(dre_channel_id)
        if days_ago < _DIGEST_INTERVAL_DAYS:
            logger.info(f"Digest DRE skip — wysłany {days_ago}d temu (Slack history guard)")
            return
    except Exception as _e:
        logger.warning(f"Digest guard error (ignoruję): {_e}")

    try:
        dre_channel_id = os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")
        logger.info("🔥 Generuję Daily Digest dla DRE...")

        main_msg, thread_msg = generate_daily_digest_dre()

        # 1. Wyślij wiadomość główną
        resp = _ctx.app.client.chat_postMessage(channel=dre_channel_id, text=main_msg)
        logger.info("✅ Daily Digest (main) wysłany!")

        # 2. Wyślij szczegóły w threadzie (jeśli są)
        if thread_msg and resp.get("ts"):
            _ctx.app.client.chat_postMessage(
                channel=dre_channel_id,
                thread_ts=resp["ts"],
                text=thread_msg,
            )
            logger.info("✅ Daily Digest (thread) wysłany!")

    except Exception as e:
        logger.error(f"❌ Błąd wysyłania digestu: {e}")
