"""Weekly cost report — co poniedziałek 9:05 na #zarzondpato.

Pokazuje:
- Koszt planu Claude Teams podzielony per pracownik
- Koszt tokenów API Sebol (bot) per pracownik i per model
- Łączny koszt AI na osobę i dla całego teamu

Konfiguracja env (opcjonalna — domyślnie 125 EUR):
  TEAMS_PLAN_MONTHLY_EUR  — miesięczna opłata za Claude Teams w EUR (domyślnie 125)
  EUR_TO_PLN              — kurs EUR/PLN (domyślnie 4.25)
  ZARZAD_CHANNEL_ID       — opcjonalnie, domyślnie C0AJ4HBS94G
"""
import os
import logging
from datetime import datetime

import _ctx
from config.constants import TEAM_MEMBERS
from tools.token_log import get_summary, get_user_summary, USD_TO_PLN

logger = logging.getLogger(__name__)

ZARZAD_CHANNEL = os.environ.get("ZARZAD_CHANNEL_ID", "C0AJ4HBS94G")
_SLACK_ID_TO_NAME = {m["slack_id"]: m["name"] for m in TEAM_MEMBERS}

EUR_TO_PLN = float(os.environ.get("EUR_TO_PLN", "4.25"))
TEAMS_PLAN_MONTHLY_EUR = float(os.environ.get("TEAMS_PLAN_MONTHLY_EUR", "125"))


def _teams_monthly_pln() -> float:
    return TEAMS_PLAN_MONTHLY_EUR * EUR_TO_PLN


def generate_weekly_cost_report(days: int = 7) -> str:
    now = datetime.now()
    week_label = f"{(now - __import__('datetime').timedelta(days=days)).strftime('%d.%m')}–{now.strftime('%d.%m.%Y')}"
    n_members = len(TEAM_MEMBERS)

    lines = [f"💰 *Tygodniowy raport kosztów AI — {week_label}*\n"]

    # ── Sekcja 1: Plan Claude Teams ──────────────────────────────────────────
    monthly_pln = _teams_monthly_pln()
    weekly_plan_pln = monthly_pln / 4.33
    per_person_plan = weekly_plan_pln / n_members if n_members else 0
    lines.append("*📋 Plan Claude Teams (Standard)*")
    lines.append(
        f"  Stała opłata: *€{TEAMS_PLAN_MONTHLY_EUR:.0f}/mc* ({monthly_pln:.0f} PLN)"
        f"  →  tygodniowo: *{weekly_plan_pln:.0f} PLN*"
    )
    lines.append(f"  Per osoba: *{per_person_plan:.0f} PLN/tydzień*  _(6 osób × €{TEAMS_PLAN_MONTHLY_EUR/n_members:.2f})_\n")

    # ── Sekcja 2: Tokeny API Sebol (bot) ─────────────────────────────────────
    _, total = get_summary(days=days)
    bot_total_pln = float(total["cost_pln"]) if total and total["cost_pln"] else 0
    bot_calls = int(total["calls"]) if total and total["calls"] else 0
    bot_tokens_in = int(total["input_tokens"]) if total and total["input_tokens"] else 0
    bot_tokens_out = int(total["output_tokens"]) if total and total["output_tokens"] else 0

    lines.append("*🤖 Sebol — tokeny API (Slack bot)*")
    lines.append(
        f"  Łącznie: *{bot_total_pln:.3f} PLN*"
        f"  |  {bot_calls} wywołań  |  {bot_tokens_in:,} in / {bot_tokens_out:,} out"
    )

    user_rows = get_user_summary(days=days)
    if user_rows:
        lines.append("  *Per osoba (kto używał bota):*")
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(user_rows):
            uid = row["user_id"]
            name = _SLACK_ID_TO_NAME.get(uid) or f"<@{uid}>"
            medal = medals[i] if i < 3 else f"  {i+1}."
            pct = (row["cost_pln"] / bot_total_pln * 100) if bot_total_pln else 0
            lines.append(
                f"  {medal} *{name}*: `{row['cost_pln']:.3f} PLN`"
                f"  ({pct:.0f}%,  {row['calls']} wywołań)"
            )
    lines.append("")

    # ── Sekcja 3: Podsumowanie łączne ────────────────────────────────────────
    total_pln = weekly_plan_pln + bot_total_pln
    per_person_total = total_pln / n_members if n_members else 0
    monthly_est_pln = monthly_pln + bot_total_pln * 4.33

    lines.append("*📊 Łącznie (plan + bot)*")
    lines.append(f"  Koszt tygodnia: *{total_pln:.2f} PLN*  (plan: {weekly_plan_pln:.0f} + bot: {bot_total_pln:.2f})")
    lines.append(f"  Per osoba (średnia): *{per_person_total:.2f} PLN/tydzień*")
    lines.append(f"  Szacowany miesięczny koszt AI: *~{monthly_est_pln:.0f} PLN*  (~€{monthly_est_pln/EUR_TO_PLN:.0f})")

    lines.append(f"\n_Wygenerowano przez Sebol • {now.strftime('%d.%m.%Y %H:%M')}_")
    return "\n".join(lines)


def weekly_cost_report():
    """Wysyła tygodniowy raport kosztów AI na #zarzondpato. Poniedziałek 9:05."""
    try:
        logger.info("💰 Generuję tygodniowy raport kosztów...")
        report = generate_weekly_cost_report(days=7)
        _ctx.app.client.chat_postMessage(channel=ZARZAD_CHANNEL, text=report)
        logger.info("✅ Weekly cost report wysłany na #zarzondpato")
    except Exception as e:
        logger.error(f"❌ Błąd weekly_cost_report: {e}")
