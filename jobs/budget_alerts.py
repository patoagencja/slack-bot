"""Budget alerts + weekly summary formatting."""
import os
import logging
import pytz
from datetime import datetime

import _ctx
from tools.meta_ads import meta_ads_tool
from tools.google_ads import google_ads_tool
from jobs.performance_analysis import analyze_campaign_trends

logger = logging.getLogger(__name__)

# Cooldown tracking: {alert_key: datetime}
sent_alerts = {}


def format_budget_alert(alert):
    """Formatuje alert budżetowy"""
    emoji = "🔴" if alert["level"] == "CRITICAL" else "🟡"
    action = "⛔ AKCJA: Zredukuj budget TERAZ!" if alert["level"] == "CRITICAL" else "👀 Monitoruj - możliwy overspend"
    return (
        f"{emoji} *BUDGET ALERT - {alert['level']}*\n"
        f"📌 Klient: {alert['client'].upper()} ({alert['platform']})\n"
        f"📢 Kampania: {alert['campaign']}\n"
        f"💰 Spend dzisiaj: {alert['spend']:.2f} PLN\n"
        f"📈 Pace: {alert['pace']:.0f}% daily budget\n"
        f"{action}"
    )


def format_weekly_summary(client_name, data, period):
    """Formatuje tygodniowy raport dla klienta"""
    if not data:
        return f"📊 *{client_name.upper()}* - brak danych za {period}"

    total_spend = sum(c.get("spend", 0) or c.get("cost", 0) for c in data)
    total_conversions = sum(c.get("conversions", 0) for c in data)
    total_clicks = sum(c.get("clicks", 0) for c in data)

    roas_values = [c.get("purchase_roas", 0) for c in data if c.get("purchase_roas", 0) > 0]
    avg_roas = sum(roas_values) / len(roas_values) if roas_values else 0

    analysis = analyze_campaign_trends(data)

    roas_line = ""
    if avg_roas > 0:
        roas_emoji = "✅" if avg_roas >= 3.0 else ("🟡" if avg_roas >= 2.0 else "🔴")
        roas_line = f"📈 Avg ROAS: {avg_roas:.2f} {roas_emoji}\n"

    report = (
        f"📊 *{client_name.upper()} - Weekly Report* ({period})\n\n"
        f"💰 SPEND: {total_spend:.2f} PLN\n"
        f"🎯 Conversions: {total_conversions}\n"
        f"👆 Clicks: {total_clicks:,}\n"
        f"{roas_line}"
        f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if analysis["critical_alerts"]:
        report += "\n🔴 *WYMAGA UWAGI:*\n"
        for alert in analysis["critical_alerts"][:3]:
            report += f"• **{alert['campaign']}**: {alert['message']}\n"
            if alert.get("action"):
                report += f"  💡 {alert['action']}\n"

    if analysis["top_performers"]:
        report += "\n🔥 *TOP PERFORMERS:*\n"
        for top in analysis["top_performers"][:3]:
            report += f"• **{top['campaign']}** — {top.get('metrics_line', '')}\n"

    if analysis["warnings"]:
        report += "\n🟡 *DO OBEJRZENIA:*\n"
        for w in analysis["warnings"][:2]:
            report += f"• **{w['campaign']}**: {w['message']}\n"

    return report


def should_send_alert(alert_key, cooldown_hours=4):
    """Sprawdza czy alert był już wysłany w ostatnich X godzinach"""
    if alert_key in sent_alerts:
        hours_ago = (datetime.now() - sent_alerts[alert_key]).total_seconds() / 3600
        if hours_ago < cooldown_hours:
            return False
    return True


def mark_alert_sent(alert_key):
    sent_alerts[alert_key] = datetime.now()


def check_budget_alerts():
    """
    Sprawdza budget pace dla wszystkich klientów i wysyła alerty.
    Uruchamiane co godzinę (7:00-22:00).
    """
    try:
        warsaw_tz = pytz.timezone('Europe/Warsaw')
        now = datetime.now(warsaw_tz)

        if now.hour < 7 or now.hour >= 22:
            return

        day_progress = (now.hour * 60 + now.minute) / (24 * 60)
        today = now.strftime('%Y-%m-%d')

        alerts_to_send = []

        clients_meta = [
            ("drzwi dre", os.environ.get("DRE_CHANNEL_ID")),
            ("instax/fuji", os.environ.get("INSTAX_CHANNEL_ID")),
            ("zbiorcze", os.environ.get("GENERAL_CHANNEL_ID")),
        ]

        for client_name, channel_id in clients_meta:
            if not channel_id:
                continue
            try:
                data = meta_ads_tool(
                    client_name=client_name,
                    date_from=today,
                    date_to=today,
                    level="campaign",
                    metrics=["campaign_name", "spend", "budget_remaining"]
                )
                for campaign in data.get("data", []):
                    spend = float(campaign.get("spend", 0))
                    remaining = campaign.get("budget_remaining")
                    if spend < 10 or remaining is None:
                        continue
                    total_budget = spend + float(remaining)
                    if total_budget <= 0:
                        continue
                    pace = (spend / total_budget) / max(day_progress, 0.01)
                    campaign_name = campaign.get("campaign_name", "Unknown")
                    base_key = f"meta_{client_name}_{campaign_name}_{today}"

                    if pace > 1.5 and should_send_alert(base_key + "_crit"):
                        alerts_to_send.append({
                            "level": "CRITICAL", "platform": "Meta",
                            "client": client_name, "campaign": campaign_name,
                            "spend": spend, "pace": pace * 100,
                            "channel": channel_id, "alert_key": base_key + "_crit"
                        })
                    elif pace > 1.2 and should_send_alert(base_key + "_warn"):
                        alerts_to_send.append({
                            "level": "WARNING", "platform": "Meta",
                            "client": client_name, "campaign": campaign_name,
                            "spend": spend, "pace": pace * 100,
                            "channel": channel_id, "alert_key": base_key + "_warn"
                        })
            except Exception as e:
                logger.error(f"Budget alert Meta {client_name}: {e}")

        for alert in alerts_to_send:
            try:
                _ctx.app.client.chat_postMessage(
                    channel=alert["channel"],
                    text=format_budget_alert(alert)
                )
                mark_alert_sent(alert["alert_key"])
                logger.info(f"Budget alert: {alert['level']} - {alert['campaign']}")
            except Exception as e:
                logger.error(f"Błąd wysyłania alertu: {e}")

    except Exception as e:
        logger.error(f"Błąd check_budget_alerts: {e}")


def check_budget_status(client_name, platform):
    """
    Pobiera spend vs daily budget dla klienta.
    Zwraca listę kampanii z alertami: >80% 🟡, >90% 🟠, >100% 🔴
    """
    today = datetime.now().strftime('%Y-%m-%d')
    alerts = []

    try:
        if platform == "meta":
            data = meta_ads_tool(
                client_name=client_name,
                date_from=today,
                date_to=today,
                level="campaign",
                metrics=["campaign_name", "spend", "budget_remaining"]
            )
            for campaign in data.get("data", []):
                spend = float(campaign.get("spend", 0))
                remaining = campaign.get("budget_remaining")
                if spend < 1 or remaining is None:
                    continue
                total = spend + float(remaining)
                if total <= 0:
                    continue
                pct = (spend / total) * 100
                if pct >= 80:
                    alerts.append({
                        "campaign": campaign.get("campaign_name", "Unknown"),
                        "spend": spend,
                        "total": total,
                        "pct": pct
                    })

        elif platform == "google":
            data = google_ads_tool(
                client_name=client_name,
                date_from=today,
                date_to=today,
                level="campaign",
                metrics=["campaign.name", "metrics.cost_micros"]
            )
            for campaign in data.get("data", []):
                cost = campaign.get("cost", 0)
                if cost > 10:
                    alerts.append({
                        "campaign": campaign.get("name", "Unknown"),
                        "spend": cost,
                        "total": None,
                        "pct": None
                    })

    except Exception as e:
        logger.error(f"check_budget_status {client_name}/{platform}: {e}")

    return alerts


def send_budget_alerts_dre():
    """
    Sprawdza budgety dla wszystkich kont DRE i wysyła alert na #drzwi-dre.
    Uruchamiane co 2 godziny (9:00-19:00).
    """
    try:
        warsaw_tz = pytz.timezone('Europe/Warsaw')
        now = datetime.now(warsaw_tz)

        if now.hour < 9 or now.hour >= 19:
            return

        dre_channel = os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")
        alert_lines = []

        meta_alerts = check_budget_status("drzwi dre", "meta")
        for a in meta_alerts:
            pct = a["pct"]
            emoji = "🔴" if pct >= 100 else ("🟠" if pct >= 90 else "🟡")
            line = f"{emoji} [Meta] {a['campaign']}: {a['spend']:.0f}/{a['total']:.0f} PLN ({pct:.0f}%)"
            alert_lines.append((pct, line))

        for account in ["dre", "dre 2024", "dre 2025"]:
            google_alerts = check_budget_status(account, "google")
            for a in google_alerts:
                line = f"📊 [Google/{account}] {a['campaign']}: {a['spend']:.0f} PLN spend today"
                alert_lines.append((0, line))

        if not alert_lines:
            return

        alert_lines.sort(key=lambda x: x[0], reverse=True)

        msg = f"⚠️ *BUDGET ALERT - DRE* ({now.strftime('%H:%M')})\n\n"
        msg += "\n".join(line for _, line in alert_lines)
        msg += "\n\n_Sprawdź kampanie i zredukuj budget jeśli potrzeba._"

        _ctx.app.client.chat_postMessage(channel=dre_channel, text=msg)
        logger.info(f"Budget alert DRE wysłany: {len(alert_lines)} kampanii")

    except Exception as e:
        logger.error(f"Błąd send_budget_alerts_dre: {e}")
