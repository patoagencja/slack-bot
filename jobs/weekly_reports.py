"""Weekly auto-reports DRE — piątek 16:00."""
import os
import logging
import pytz
from datetime import datetime, timedelta

import _ctx
from tools.meta_ads import meta_ads_tool
from tools.google_ads import google_ads_tool
from jobs.budget_alerts import format_weekly_summary

logger = logging.getLogger(__name__)


def generate_weekly_report_dre():
    """
    Generuje tygodniowy raport DRE z week-over-week comparison.
    Meta + Google, top/worst performers, rekomendacje.
    """
    now = datetime.now()
    date_to   = now.strftime('%Y-%m-%d')
    date_from = (now - timedelta(days=7)).strftime('%Y-%m-%d')
    prev_to   = (now - timedelta(days=8)).strftime('%Y-%m-%d')
    prev_from = (now - timedelta(days=14)).strftime('%Y-%m-%d')
    period_label = f"{(now - timedelta(days=7)).strftime('%d.%m')} - {now.strftime('%d.%m.%Y')}"

    def fetch_dre_data(d_from, d_to):
        campaigns = []
        meta = meta_ads_tool(
            client_name="drzwi dre",
            date_from=d_from, date_to=d_to,
            level="campaign",
            metrics=["campaign_name", "spend", "clicks", "impressions",
                     "ctr", "cpc", "conversions", "purchase_roas", "frequency"]
        )
        if meta.get("data"):
            for c in meta["data"]:
                c["_platform"] = "Meta"
            campaigns.extend(meta["data"])
        for account in ["dre", "dre 2024", "dre 2025"]:
            g = google_ads_tool(
                client_name=account,
                date_from=d_from, date_to=d_to,
                level="campaign",
                metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                         "metrics.cost_micros", "metrics.conversions", "metrics.ctr",
                         "metrics.average_cpc"]
            )
            if g.get("data"):
                for c in g["data"]:
                    c["_platform"] = f"Google/{account}"
                    c.setdefault("campaign_name", c.get("name", "Unknown"))
                    c.setdefault("spend", c.get("cost", 0))
                campaigns.extend(g["data"])
        return campaigns

    try:
        this_week = fetch_dre_data(date_from, date_to)
        prev_week = fetch_dre_data(prev_from, prev_to)

        if not this_week:
            return "📊 *DRE Weekly Report* - brak danych za ten tydzień."

        def totals(data):
            return {
                "spend":       sum(c.get("spend", 0) or c.get("cost", 0) for c in data),
                "conversions": sum(c.get("conversions", 0) for c in data),
                "clicks":      sum(c.get("clicks", 0) for c in data),
            }

        cur = totals(this_week)
        prv = totals(prev_week)

        def delta(cur_val, prv_val):
            if prv_val == 0:
                return ""
            pct = ((cur_val - prv_val) / prv_val) * 100
            arrow = "↑" if pct >= 0 else "↓"
            return f" ({arrow}{abs(pct):.0f}% vs prev week)"

        meta_camps = [c for c in this_week if c.get("_platform") == "Meta" and c.get("purchase_roas", 0) > 0]
        meta_camps_sorted = sorted(meta_camps, key=lambda x: x.get("purchase_roas", 0), reverse=True)
        top3   = meta_camps_sorted[:3]
        worst3 = meta_camps_sorted[-3:][::-1] if len(meta_camps_sorted) >= 3 else []

        recommendations = []
        for c in worst3:
            roas = c.get("purchase_roas", 0)
            freq = c.get("frequency", 0)
            ctr  = c.get("ctr", 0)
            name = c.get("campaign_name", "?")
            if roas < 2.0:
                recommendations.append(f"🔴 Pause lub optymalizuj *{name}* (ROAS {roas:.1f})")
            elif freq > 4:
                recommendations.append(f"🟡 Odśwież kreacje *{name}* (Frequency {freq:.1f})")
            elif ctr < 0.8:
                recommendations.append(f"🟡 Zmień targeting *{name}* (CTR {ctr:.2f}%)")

        for c in top3[:1]:
            name = c.get("campaign_name", "?")
            roas = c.get("purchase_roas", 0)
            recommendations.append(f"🚀 Skaluj *{name}* (ROAS {roas:.1f} — top performer!)")

        if not recommendations:
            recommendations.append("✅ Wszystkie kampanie w normie — monitoruj dalej.")

        report = f"📊 *DRE - Weekly Report* ({period_label})\n\n"
        report += (
            f"💰 *SPEND:* {cur['spend']:.0f} PLN{delta(cur['spend'], prv['spend'])}\n"
            f"🎯 *CONVERSIONS:* {cur['conversions']}{delta(cur['conversions'], prv['conversions'])}\n"
            f"👆 *CLICKS:* {cur['clicks']:,}{delta(cur['clicks'], prv['clicks'])}\n"
        )
        report += "\n━━━━━━━━━━━━━━━━━━━━━━\n"

        if top3:
            report += "\n🏆 *TOP PERFORMERS:*\n"
            for i, c in enumerate(top3, 1):
                roas  = c.get("purchase_roas", 0)
                conv  = c.get("conversions", 0)
                spend = c.get("spend", 0)
                report += f"{i}. {c.get('campaign_name', '?')} — ROAS {roas:.1f} | {conv} conv | {spend:.0f} PLN\n"

        if worst3:
            report += "\n⚠️ *WORST PERFORMERS:*\n"
            for i, c in enumerate(worst3, 1):
                roas  = c.get("purchase_roas", 0)
                ctr   = c.get("ctr", 0)
                spend = c.get("spend", 0)
                report += f"{i}. {c.get('campaign_name', '?')} — ROAS {roas:.1f} | CTR {ctr:.2f}% | {spend:.0f} PLN\n"

        report += "\n💡 *NEXT WEEK ACTIONS:*\n"
        for rec in recommendations[:3]:
            report += f"• {rec}\n"

        report += f"\n_Raport tygodniowy | {now.strftime('%d.%m.%Y %H:%M')}_"
        return report

    except Exception as e:
        logger.error(f"Błąd generate_weekly_report_dre: {e}")
        return f"❌ Błąd generowania raportu: {str(e)}"


def weekly_report_dre():
    """Wysyła weekly report DRE na DRE_CHANNEL_ID. Piątek 16:00."""
    try:
        dre_channel = os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")
        logger.info("📊 Generuję Weekly Report DRE...")
        report = generate_weekly_report_dre()
        _ctx.app.client.chat_postMessage(channel=dre_channel, text=report)
        logger.info("✅ Weekly Report DRE wysłany!")
    except Exception as e:
        logger.error(f"❌ Błąd weekly_report_dre: {e}")


def send_weekly_reports():
    """
    Wysyła tygodniowe raporty performance dla klientów.
    Uruchamiane w piątek o 16:00.
    """
    try:
        warsaw_tz = pytz.timezone('Europe/Warsaw')
        now       = datetime.now(warsaw_tz)
        date_to   = now.strftime('%Y-%m-%d')
        date_from = (now - timedelta(days=7)).strftime('%Y-%m-%d')
        period    = f"{(now - timedelta(days=7)).strftime('%d.%m')} - {now.strftime('%d.%m.%Y')}"

        logger.info(f"📊 Generuję Weekly Reports za {period}...")

        dre_channel = os.environ.get("DRE_CHANNEL_ID")

        if dre_channel:
            meta_data = meta_ads_tool(
                client_name="drzwi dre",
                date_from=date_from, date_to=date_to,
                level="campaign",
                metrics=["campaign_name", "spend", "clicks", "ctr", "cpc",
                         "conversions", "purchase_roas", "impressions", "frequency"]
            )

            google_data = []
            for account in ["dre", "dre 2025"]:
                data = google_ads_tool(
                    client_name=account,
                    date_from=date_from, date_to=date_to,
                    level="campaign",
                    metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                             "metrics.cost_micros", "metrics.conversions", "metrics.ctr"]
                )
                if data.get("data"):
                    google_data.extend(data["data"])

            all_dre = []
            if meta_data.get("data"):
                all_dre.extend(meta_data["data"])
            all_dre.extend(google_data)

            report  = format_weekly_summary("DRE", all_dre, period)
            report += f"\n\n_Raport tygodniowy | {now.strftime('%d.%m.%Y %H:%M')}_"

            _ctx.app.client.chat_postMessage(channel=dre_channel, text=report)
            logger.info("✅ Weekly Report DRE wysłany!")

    except Exception as e:
        logger.error(f"❌ Błąd send_weekly_reports: {e}")
