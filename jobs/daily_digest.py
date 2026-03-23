"""Daily digest + weekly learnings for DRE."""
import os
import logging
from datetime import datetime, timedelta

import _ctx
from config.constants import CLIENT_GOALS, _DIGEST_INTERVAL_DAYS, AD_CLIENTS
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
                         google_spend=0, google_ctr=0, google_conversions=0,
                         meta_error=False, google_error=False,
                         # month-to-date totals
                         mtd_meta_spend=None, mtd_google_spend=None,
                         mtd_meta_conversions=None, mtd_google_conversions=None):
    """Buduje krótką wiadomość główną (widoczna na kanale)."""
    skipped_note = f" _(+{skipped_count} poniżej 20 PLN pominięto)_" if skipped_count > 0 else ""
    show_both = meta_count > 0 or google_count > 0 or meta_error or google_error

    lines = [
        f"📊 *META + GOOGLE ADS – DRE | {date_label}*{skipped_note}",
        "",
    ]

    # META section
    if meta_error:
        lines += [
            f"🔵 *META ADS* — ⚠️ _Brak danych (błąd API Meta — spróbuj później)_",
            "",
        ]
    elif meta_count > 0:
        meta_mtd = ""
        if mtd_meta_spend is not None:
            meta_mtd = f" _(miesiąc: {mtd_meta_spend:.0f} PLN"
            if mtd_meta_conversions is not None:
                meta_mtd += f" | {mtd_meta_conversions} konwersji"
            meta_mtd += ")_"
        lines += [
            f"🔵 *META ADS* — {meta_count} kampanii",
            f"   💰 Spend 7d: *{meta_spend:.0f} PLN*{meta_mtd} | 👥 Reach: *{meta_reach:,}* | 📈 CTR: *{meta_ctr:.2f}%* | 🎯 Konwersje: *{meta_conversions}*",
            "",
        ]

    # GOOGLE section
    if google_error:
        lines += [
            f"🔴 *GOOGLE ADS* — ⚠️ _Błąd API Google — spróbuj później_",
            "",
        ]
    elif google_count == 0:
        lines += [
            f"🔴 *GOOGLE ADS* — ⚠️ _Brak danych (brak kampanii z wydatkiem ≥20 PLN lub błąd API)_",
            "",
        ]
    elif google_count > 0:
        google_mtd = ""
        if mtd_google_spend is not None:
            google_mtd = f" _(miesiąc: {mtd_google_spend:.0f} PLN"
            if mtd_google_conversions is not None:
                google_mtd += f" | {mtd_google_conversions} konwersji"
            google_mtd += ")_"
        lines += [
            f"🔴 *GOOGLE ADS* — {google_count} kampanii",
            f"   💰 Spend 7d: *{google_spend:.0f} PLN*{google_mtd} | 📈 Avg CTR: *{google_ctr:.2f}%* | 🎯 Konwersje: *{google_conversions}*",
            "",
        ]

    lines.append(f"📣 Łącznie aktywnych kampanii: *{campaign_count}*")

    # Alerty
    meta_alerts   = [a for a in obj_alerts if '(Google)' not in a['campaign']]
    google_alerts = [a for a in obj_alerts if '(Google)' in a['campaign']]

    if obj_alerts:
        lines.append("")
        lines.append("⚠️ *ALERTY*")
        if meta_alerts:
            for alert in meta_alerts:
                lines.append(f"• *{alert['campaign']}* — {alert['message']}")
        if google_alerts:
            lines.append("_Google:_")
            for alert in google_alerts:
                lines.append(f"• *{alert['campaign']}* — {alert['message']}")
    else:
        lines.append("")
        lines.append("✅ Brak alertów — wszystko wygląda OK")

    lines.append("")
    lines.append("💬 _Szczegółowa analiza kampanii w threadzie ↓_")

    return "\n".join(lines)


def _engagement_rate(c):
    """Engagement rate (%) dla kampanii Meta."""
    eng = c.get('_engagement', {})
    total = sum(eng.values()) if isinstance(eng, dict) else 0
    reach = int(c.get('reach', 0) or 0)
    return (total / reach * 100) if reach > 0 else 0.0


def _total_eng(c):
    """Suma interakcji dla kampanii Meta."""
    eng = c.get('_engagement', {})
    return sum(eng.values()) if isinstance(eng, dict) else 0


def _build_thread_message(meta_campaigns, google_campaigns, obj_alerts, experiments, smart_recs):
    """Krótka, decyzyjna analiza do threadu — max ~15 linii."""
    gc = google_campaigns or []

    # ── Kategoryzacja Meta po celu ────────────────────────────────────────────
    reach_camps  = sorted(
        [c for c in meta_campaigns if c.get('_objective') == 'reach'],
        key=lambda c: int(c.get('reach', 0) or 0), reverse=True
    )
    eng_camps    = [c for c in meta_campaigns if c.get('_objective') == 'engagement']
    conv_camps   = [c for c in meta_campaigns if c.get('_objective') == 'conversion']
    traffic_camps = [c for c in meta_campaigns if c.get('_objective') == 'traffic']

    total_convs_meta   = sum(int(c.get('conversions', 0) or 0) for c in conv_camps)
    total_convs_google = sum(int(c.get('conversions', 0) or 0) for c in gc)

    # ── TL;DR ─────────────────────────────────────────────────────────────────
    tldr = []
    if reach_camps:
        top3 = reach_camps[:3]
        avg_reach = sum(int(c.get('reach', 0) or 0) for c in top3) // len(top3)
        names = [c.get('campaign_name', '?').split(' - ')[1] if ' - ' in c.get('campaign_name', '') else c.get('campaign_name', '?')
                 for c in top3]
        tldr.append(f"Reach działa najlepiej: {', '.join(names[:3])}")
    if eng_camps:
        good_eng = [c for c in eng_camps if _engagement_rate(c) >= 3]
        if good_eng:
            tldr.append(f"Engagement skuteczny w {len(good_eng)}/{len(eng_camps)} kampaniach — bez przełożenia na konwersje")
        else:
            tldr.append(f"Kampanie Engagement ({len(eng_camps)}) — wyniki przeciętne lub brak interakcji")
    if conv_camps and total_convs_meta == 0 and total_convs_google == 0:
        conv_spend = sum(float(c.get('spend', 0) or 0) for c in conv_camps)
        tldr.append(f"Brak konwersji — {len(conv_camps)} kampanii konwersji bez wyniku przy {conv_spend:.0f} PLN")
    elif total_convs_meta + total_convs_google > 0:
        tldr.append(f"Łącznie {total_convs_meta + total_convs_google} konwersji (Meta: {total_convs_meta} | Google: {total_convs_google})")
    tldr = tldr[:3]

    # ── TOP KAMPANIE (max 3) ──────────────────────────────────────────────────
    top = []
    for c in reach_camps[:3]:
        name  = c.get('campaign_name', '?')
        reach = int(c.get('reach', 0) or 0)
        freq  = c.get('frequency') or 0
        top.append(f"{name} | Reach | zasięg {reach:,} | freq {freq:.1f}")
    if not top:
        best_eng = sorted(eng_camps, key=_engagement_rate, reverse=True)
        for c in best_eng[:2]:
            if _engagement_rate(c) >= 1:
                name = c.get('campaign_name', '?')
                er   = _engagement_rate(c)
                top.append(f"{name} | Engagement | eng. rate {er:.1f}%")
    if not top:
        for c in sorted(gc, key=lambda c: int(c.get('conversions', 0) or 0), reverse=True)[:2]:
            if int(c.get('conversions', 0) or 0) > 0:
                name  = c.get('campaign_name', c.get('name', '?'))
                convs = int(c.get('conversions', 0) or 0)
                top.append(f"{name} | Google | {convs} konwersji")
    top = top[:3]

    # ── DO OBSERWACJI (max 3, agregowane) ────────────────────────────────────
    watch = []
    mod_eng = [c for c in eng_camps if 0 < _engagement_rate(c) < 3]
    zero_eng = [c for c in eng_camps if _total_eng(c) == 0 and int(c.get('reach', 0) or 0) > 200]
    if len(mod_eng) > 1:
        watch.append(f"{len(mod_eng)} kampanii Engagement — przeciętny wynik, łącznie {sum(_total_eng(c) for c in mod_eng)} interakcji")
    elif len(mod_eng) == 1:
        c = mod_eng[0]
        watch.append(f"{c.get('campaign_name', '?')} | Engagement | {_total_eng(c)} interakcji — monitoruj")
    if zero_eng and len(watch) < 3:
        watch.append(f"{len(zero_eng)} kampanii Engagement bez interakcji — sprawdź kreacje")
    high_freq = [c for c in reach_camps if float(c.get('frequency') or 0) >= 4]
    if high_freq and len(watch) < 3:
        watch.append(f"Ad fatigue: {len(high_freq)} kampanii Reach z frequency ≥ 4 — wymień kreacje")
    g_no_conv = [c for c in gc if int(c.get('conversions', 0) or 0) == 0 and float(c.get('cost', c.get('spend', 0)) or 0) > 50]
    if g_no_conv and len(watch) < 3:
        watch.append(f"Google: {len(g_no_conv)} kampanii bez konwersji — sprawdź słowa kluczowe")
    watch = watch[:3]

    # ── PROBLEMY (max 3) ──────────────────────────────────────────────────────
    problems = []
    no_conv = [c for c in conv_camps if int(c.get('conversions', 0) or 0) == 0 and float(c.get('spend', 0) or 0) > 50]
    if no_conv:
        spend_nc = sum(float(c.get('spend', 0) or 0) for c in no_conv)
        if len(no_conv) > 2:
            problems.append(f"{len(no_conv)} kampanii konwersji bez wyniku — {spend_nc:.0f} PLN zmarnowane")
        else:
            names = " + ".join(c.get('campaign_name', '?') for c in no_conv[:2])
            problems.append(f"{names} — 0 konwersji przy {spend_nc:.0f} PLN")
    low_roas = [c for c in conv_camps if c.get('purchase_roas') and float(c.get('purchase_roas') or 0) < 1.5
                and float(c.get('spend', 0) or 0) > 50]
    if low_roas and len(problems) < 3:
        avg_roas = sum(float(c.get('purchase_roas', 0) or 0) for c in low_roas) / len(low_roas)
        problems.append(f"ROAS {avg_roas:.1f}x < 1.5 — {len(low_roas)} kampanii poniżej break-even")
    g_search_low_ctr = [c for c in gc
                        if _google_campaign_type(c.get('campaign_name', c.get('name', ''))) == 'search'
                        and float(c.get('ctr', 0) or 0) < 1
                        and float(c.get('cost', c.get('spend', 0)) or 0) > 50]
    if g_search_low_ctr and len(problems) < 3:
        problems.append(f"Google Search: {len(g_search_low_ctr)} kampanii CTR < 1% — sprawdź reklamy")
    if zero_eng and not any('bez interakcji' in p for p in problems) and len(problems) < 3:
        spend_ze = sum(float(c.get('spend', 0) or 0) for c in zero_eng)
        if spend_ze > 100:
            problems.append(f"{len(zero_eng)} kampanii Engagement — 0 interakcji przy {spend_ze:.0f} PLN")
    problems = problems[:3]

    # ── REKOMENDACJE (max 3) ──────────────────────────────────────────────────
    recs = []
    if no_conv:
        recs.append(f"Wyłącz lub przebuduj kampanie konwersji bez wyniku ({len(no_conv)} sztuk)")
    if reach_camps and no_conv and total_convs_meta == 0:
        recs.append("Przenieś budżet z kampanii konwersji bez wyniku do najlepszych kampanii Reach")
    for rec in (smart_recs or [])[:3]:
        action = rec.get('action', '')
        if action and rec.get('confidence', 0) >= 0.6 and len(recs) < 3:
            recs.append(action)
    if experiments and len(recs) < 3:
        recs.append(f"🧪 Testuj: {experiments[0]['experiment']}")
    if not recs:
        seen = set()
        for alert in obj_alerts:
            action = alert.get('action', '')
            if action and action not in seen and len(recs) < 3:
                recs.append(action)
                seen.add(action)
    recs = recs[:3]

    # ── Buduj wiadomość ───────────────────────────────────────────────────────
    lines = []
    if tldr:
        lines.append("🧠 *TL;DR*")
        for t in tldr:
            lines.append(f"• {t}")
        lines.append("")

    if top:
        lines.append("🟢 *TOP KAMPANIE*")
        for t in top:
            lines.append(f"• {t}")
        lines.append("")

    if watch:
        lines.append("🟡 *DO OBSERWACJI*")
        for w in watch:
            lines.append(f"• {w}")
        lines.append("")

    if problems:
        lines.append("🔴 *PROBLEMY*")
        for p in problems:
            lines.append(f"• {p}")
        lines.append("")

    if recs:
        lines.append("🎯 *REKOMENDACJE*")
        for r in recs:
            lines.append(f"• {r}")
    elif not (tldr or top or watch or problems):
        lines.append("✅ Kampanie działają stabilnie — brak alertów.")

    return "\n".join(lines)


# ── WEEKLY LEARNINGS ───────────────────────────────────────────────────────────

def _aggregate_campaign_stats(entries):
    """Sum up spend/clicks and compute weighted avg CTR from a list of daily entries."""
    total_spend = sum(float(e.get("spend") or 0) for e in entries)
    total_clicks = sum(int(e.get("clicks") or 0) for e in entries)
    total_impressions = sum(int(e.get("impressions") or 0) for e in entries)
    avg_ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0.0
    return total_spend, total_clicks, avg_ctr


def _fetch_weekly_learnings_data():
    """Pobiera dane Meta + Google dla ostatnich 7 dni i bieżącego miesiąca."""
    from tools.meta_ads import meta_ads_tool
    from tools.google_ads import google_ads_tool

    now = datetime.now()
    yesterday   = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    week_ago    = (now - timedelta(days=7)).strftime('%Y-%m-%d')
    month_start = now.replace(day=1).strftime('%Y-%m-%d')

    results = {}

    for label, date_from, date_to in [
        ("week",  week_ago,    yesterday),
        ("month", month_start, yesterday),
    ]:
        meta_raw, google_raw = [], []
        try:
            md = meta_ads_tool(
                client_name="drzwi dre",
                date_from=date_from, date_to=date_to,
                level="campaign",
                metrics=["campaign_name", "spend", "impressions", "clicks", "ctr", "conversions"],
            )
            meta_raw = md.get("data", [])
        except Exception as e:
            logger.warning(f"weekly_learnings meta {label}: {e}")

        for account in AD_CLIENTS.get("dre", {}).get("google_accounts", ["dre", "dre 2024", "dre 2025"]):
            try:
                gd = google_ads_tool(
                    client_name=account,
                    date_from=date_from, date_to=date_to,
                    level="campaign",
                    metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                             "metrics.cost_micros", "metrics.conversions", "metrics.ctr"],
                )
                google_raw.extend(gd.get("data", []))
            except Exception as e:
                logger.warning(f"weekly_learnings google {account} {label}: {e}")

        results[label] = {"meta": meta_raw, "google": google_raw}

    return results


def generate_weekly_learnings(client="dre"):
    """Weekly summary of predictions accuracy + learned patterns."""
    now = datetime.now()
    week_cutoff  = (now - timedelta(days=7)).strftime('%Y-%m-%d')

    # Pobierz dane bezpośrednio z API (niezależnie od historii)
    live = _fetch_weekly_learnings_data()
    week_meta    = live["week"]["meta"]
    week_google  = live["week"]["google"]
    month_meta   = live["month"]["meta"]
    month_google = live["month"]["google"]

    # Predykcje z historii (te nadal mają sens z pliku)
    data = _load_history_raw()
    predictions = data.get(client, {}).get("predictions", [])
    week_preds = [p for p in predictions if p.get("date", "") >= week_cutoff]

    patterns = analyze_patterns(client)
    summary  = patterns.get("summary", {})
    text = "🧠 *WEEKLY LEARNINGS – Co nauczyłem się w tym tygodniu:*\n\n"

    # ── Platform stats: last 7 days + current month ───────────────────────────
    has_any_data = False

    for platform, icon, w_raw, m_raw, spend_key in [
        ("meta",   "🔵 META ADS",   week_meta,   month_meta,   "spend"),
        ("google", "🔴 GOOGLE ADS", week_google, month_google, "cost"),
    ]:
        if not w_raw and not m_raw:
            continue
        has_any_data = True

        def _sum_stat(rows, key, spend_key=spend_key):
            if key == "spend":
                return sum(float(r.get(spend_key, r.get("spend", 0)) or 0) for r in rows)
            if key == "clicks":
                return sum(int(r.get("clicks", 0) or 0) for r in rows)
            if key == "impressions":
                return sum(int(r.get("impressions", 0) or 0) for r in rows)
            if key == "conversions":
                return sum(int(r.get("conversions", 0) or 0) for r in rows)
            return 0

        w_spend = _sum_stat(w_raw, "spend")
        w_clicks = _sum_stat(w_raw, "clicks")
        w_impr  = _sum_stat(w_raw, "impressions")
        w_convs = _sum_stat(w_raw, "conversions")
        w_ctr   = (w_clicks / w_impr * 100) if w_impr > 0 else 0.0

        m_spend = _sum_stat(m_raw, "spend")
        m_clicks = _sum_stat(m_raw, "clicks")
        m_impr  = _sum_stat(m_raw, "impressions")
        m_convs = _sum_stat(m_raw, "conversions")
        m_ctr   = (m_clicks / m_impr * 100) if m_impr > 0 else 0.0

        text += (
            f"📊 *{icon}*\n"
            f"   7 dni:   💰 {w_spend:.0f} PLN | 👆 {w_clicks:,} kliknięć | 📈 CTR {w_ctr:.2f}% | 🎯 {w_convs} konwersji\n"
            f"   Miesiąc: 💰 {m_spend:.0f} PLN | 👆 {m_clicks:,} kliknięć | 📈 CTR {m_ctr:.2f}% | 🎯 {m_convs} konwersji\n\n"
        )

    if not has_any_data:
        text += "ℹ️ Brak danych z API za ostatni tydzień.\n\n"

    # ── Prediction verification ────────────────────────────────────────────────
    if week_preds:
        verified = []
        for pred in week_preds:
            camp_hist  = all_hist_month.get(pred["campaign"], [])
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
            text += "━━━━━━━━━━━━━━━━━━━━━━\n"
            for v in verified[:4]:
                icon = "✅" if v["success"] else "❌"
                text += f"{icon} **{v['campaign']}** – {v['recommendation']}\n"
                text += f"   Predicted: {v.get('predicted_change_pct', 0):+.0f}% | Actual: {v.get('actual_change_pct', 0):+.0f}%\n\n"
            acc = sum(1 for v in verified if v["success"]) / len(verified) * 100
            text += f"🎯 **Accuracy: {acc:.0f}%** ({len(verified)} predictions verified)\n\n"

    # ── Learned patterns ───────────────────────────────────────────────────────
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
    """Generuje daily digest dla klienta DRE (Meta + Google Ads) — ostatnie 7 dni + miesiąc.

    Zwraca tuple (main_message, thread_message).
    """
    try:
        now        = datetime.now()
        yesterday  = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        week_ago   = (now - timedelta(days=7)).strftime('%Y-%m-%d')
        month_start = now.replace(day=1).strftime('%Y-%m-%d')
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
        meta_error = bool(meta_data.get("error"))

        # === META ADS (od początku miesiąca) ===
        meta_mtd_data = {}
        if not meta_error and month_start < week_ago:
            meta_mtd_data = meta_ads_tool(
                client_name="drzwi dre",
                date_from=month_start, date_to=yesterday,
                level="campaign",
                metrics=["campaign_name", "spend", "conversions"]
            )

        # === GOOGLE ADS (ostatnie 7 dni) ===
        google_data_combined = []
        google_fetch_errors = 0
        for account in AD_CLIENTS.get("dre", {}).get("google_accounts", ["dre", "dre 2024", "dre 2025"]):
            data = google_ads_tool(
                client_name=account,
                date_from=week_ago, date_to=yesterday,
                level="campaign",
                metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                         "metrics.cost_micros", "metrics.conversions", "metrics.ctr",
                         "metrics.average_cpc"]
            )
            if data.get("error"):
                google_fetch_errors += 1
            elif data.get("data"):
                google_data_combined.extend(data["data"])
        # Google error tylko gdy WSZYSTKIE konta zwróciły błąd i nie ma żadnych danych
        google_error = google_fetch_errors == 3 and not google_data_combined

        # === GOOGLE ADS (od początku miesiąca) ===
        google_mtd_combined = []
        if month_start < week_ago:
            for account in AD_CLIENTS.get("dre", {}).get("google_accounts", ["dre", "dre 2024", "dre 2025"]):
                data = google_ads_tool(
                    client_name=account,
                    date_from=month_start, date_to=yesterday,
                    level="campaign",
                    metrics=["campaign.name", "metrics.cost_micros", "metrics.conversions"]
                )
                if data.get("data"):
                    google_mtd_combined.extend(data["data"])

        meta_campaigns_raw  = meta_data.get("data", [])
        all_campaigns_raw   = meta_campaigns_raw + google_data_combined

        if not all_campaigns_raw and not meta_error and not google_error:
            error_main = f"📊 *META + GOOGLE ADS – DRE | {date_label}*\n\n⚠️ Brak danych za ostatnie 7 dni. Sprawdź czy kampanie są aktywne."
            return error_main, None

        MIN_SPEND_PLN = 20.0
        meta_campaigns       = [c for c in meta_campaigns_raw
                                 if float(c.get("spend", 0) or 0) >= MIN_SPEND_PLN]
        google_data_filtered = [c for c in google_data_combined
                                 if float(c.get("cost", c.get("spend", 0)) or 0) >= MIN_SPEND_PLN]
        all_campaigns = meta_campaigns + google_data_filtered
        skipped_count = len(all_campaigns_raw) - len(all_campaigns)
        google_data_combined = google_data_filtered

        if not all_campaigns:
            return (
                f"📊 *META ADS – DRE | {date_label}*\n\n"
                f"⚠️ Brak kampanii z spendem ≥ 20 PLN za ostatnie 7 dni.",
                None
            )

        # Miesiąc-to-date agregaty
        mtd_meta_spend = None
        mtd_meta_conversions = None
        if meta_mtd_data.get("data"):
            mtd = meta_mtd_data["data"]
            mtd_meta_spend = sum(float(c.get("spend", 0) or 0) for c in mtd)
            mtd_meta_conversions = sum(int(c.get("conversions", 0) or 0) for c in mtd)

        mtd_google_spend = None
        mtd_google_conversions = None
        if google_mtd_combined:
            mtd_google_spend = sum(float(c.get("cost", c.get("spend", 0)) or 0) for c in google_mtd_combined)
            mtd_google_conversions = sum(int(c.get("conversions", 0) or 0) for c in google_mtd_combined)

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

        obj_alerts_raw = _build_objective_alerts(meta_campaigns, google_data_combined)
        # Deduplicate alerts by (campaign, message)
        seen_alerts = set()
        obj_alerts = []
        for a in obj_alerts_raw:
            key = (a['campaign'], a['message'])
            if key not in seen_alerts:
                seen_alerts.add(key)
                obj_alerts.append(a)

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
            meta_error=meta_error,
            google_error=google_error,
            mtd_meta_spend=mtd_meta_spend,
            mtd_meta_conversions=mtd_meta_conversions,
            mtd_google_spend=mtd_google_spend,
            mtd_google_conversions=mtd_google_conversions,
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
