"""
jobs/morning_brief.py — Unified Morning Brief for Sebol

Combines: Market Health + 3 Pillars + Leading Signals + Sector Flows
         + Macro Briefing + Watchlist Summary (KUP/CZEKAJ/OMIJAJ)

Scheduled Mon-Fri at 15:00 Warsaw (≈13:00 UTC in CEST) to replace the
separate send_daily_health_header that ran at 8:30.
"""

import datetime
import logging

import _ctx
from jobs.market_health_monitor import run_market_health
from jobs.stock_digest import fetch_macro_briefing, run_summary_digest, STOCK_CHANNEL_ID
from jobs.correction_probability import format_correction_brief

logger = logging.getLogger(__name__)

_capital_flow = None


def _get_cf():
    global _capital_flow
    if _capital_flow is None:
        try:
            import jobs.capital_flow as _capital_flow
        except Exception:
            pass
    return _capital_flow


def send_morning_brief():
    """Post Unified Morning Brief to #inwestowanie."""
    try:
        today = datetime.datetime.now().strftime("%d.%m.%Y")

        # ── 1. Market Health ──────────────────────────────────────────────────
        result = run_market_health()
        p      = result["pillars"]
        score  = result["score"]
        mode   = result["mode"]
        action = result["action"]

        pillars_line = (
            f"3 Filary: Produkcja {p['industrial_production']['label']} "
            f"| Sprzedaż {p['retail_sales']['label']} "
            f"| Zasiłki {p['jobless_claims']['label']}"
        )

        # Early-warning leading signals
        _ew_labels = {
            "B1. VIX Structure + SKEW",
            "B4. Credit Spreads (leading)",
            "B8. QQQ vs Defensive Rotation",
        }
        ew_parts = [
            r["detail"][:70].rstrip()
            for r in result["indicators"]
            if r["label"] in _ew_labels
        ]
        leading_line = " · ".join(ew_parts[:3]) if ew_parts else "—"

        # ── 2. Sector Flows ───────────────────────────────────────────────────
        cf             = _get_cf()
        top_sectors    = "—"
        bottom_sectors = "—"
        rotation_note  = ""
        if cf:
            try:
                snapshot = cf.build_capital_flow_snapshot()
                etf_perf = snapshot.get("etf_perf", {})
                if etf_perf:
                    sorted_etfs = sorted(etf_perf.items(), key=lambda x: x[1], reverse=True)
                    etf_human   = getattr(cf, "_ETF_HUMAN", {})
                    etf_sector  = getattr(cf, "SECTOR_ETFS", {})

                    def _lbl(etf, pct):
                        name = etf_human.get(etf) or etf_sector.get(etf) or etf
                        return f"{name} {pct:+.1f}%"

                    top_sectors    = " | ".join(_lbl(e, v) for e, v in sorted_etfs[:3])
                    bottom_sectors = " | ".join(_lbl(e, v) for e, v in sorted_etfs[-3:])
                    rotation_note  = snapshot.get("rotation_summary", "")
            except Exception as e:
                logger.warning("Capital flow in send_morning_brief: %s", e)

        # ── 3. Macro ──────────────────────────────────────────────────────────
        macro           = fetch_macro_briefing()
        macro_summary   = macro.get("summary", "Brak danych makro.")
        macro_risk      = macro.get("main_risk", "")
        macro_sentiment = macro.get("sentiment", "NEUTRALNY")
        s_emoji = {"RISK-ON": "🟢", "RISK-OFF": "🔴", "NEUTRALNY": "🟡"}.get(macro_sentiment, "🟡")

        # ── 4. Assemble header block ──────────────────────────────────────────
        recession_line = "\n⚠️ *RECESJA ALERT* — 2+ filary czerwone!" if result["recession_alert"] else ""

        correction_brief = format_correction_brief()
        correction_line  = f"\n{correction_brief}" if correction_brief else ""

        header = (
            f"📊 *Morning Brief — {today}*\n\n"
            f"🏥 *Market Health: {score}/100 — {mode}*{recession_line}\n"
            f"{pillars_line}\n"
            f"📡 *Leading signals:* {leading_line}\n"
            + correction_line
            + f"\n\n💰 *Kapitał płynie DO:* {top_sectors}\n"
            f"💸 *Kapitał ucieka Z:* {bottom_sectors}\n"
            + (f"↔️ Rotacja: {rotation_note}\n" if rotation_note else "")
            + f"\n{s_emoji} *Makro ({macro_sentiment}):* {macro_summary}\n"
            + (f"⚠️ *Główne ryzyko:* {macro_risk}\n" if macro_risk else "")
            + f"\n💼 *Co robić:* {action}"
        )

        _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=header)

        # ── 5. Watchlist Summary ──────────────────────────────────────────────
        digest = run_summary_digest()
        for chunk in [digest[i:i + 3900] for i in range(0, len(digest), 3900)]:
            _ctx.app.client.chat_postMessage(channel=STOCK_CHANNEL_ID, text=chunk)

        logger.info("send_morning_brief: done for %s", today)
    except Exception as e:
        logger.error("send_morning_brief failed: %s", e)
