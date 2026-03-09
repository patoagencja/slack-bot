"""Daily email summary → Slack DM do Daniela o 16:00."""
import re
import json
import time as _time
import logging
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import _ctx
from tools.email_tools import email_tool, get_user_email_config, find_unreplied_emails, _normalize_subject

logger = logging.getLogger(__name__)

DANIEL_USER_ID = "UTE1RN6SJ"


def _daniel_dm_channel() -> str:
    res = _ctx.app.client.conversations_open(users=DANIEL_USER_ID)
    return res["channel"]["id"]


def daily_email_summary_slack():
    """
    Czyta emaile z daniel@patoagencja.com, kategoryzuje przez Claude,
    wysyła podsumowanie jako Slack DM do Daniela (UTE1RN6SJ) o 16:00.
    """
    today_str  = datetime.now().strftime('%d.%m.%Y')
    today_date = datetime.now().date()

    try:
        logger.info("📧 Generuję Daily Email Summary...")
        dm_ch = _daniel_dm_channel()

        result = email_tool(user_id=DANIEL_USER_ID, action="read", limit=50, folder="INBOX")

        if "error" in result:
            _ctx.app.client.chat_postMessage(
                channel=dm_ch,
                text=f"📧 **Email Summary - {today_str}**\n\n❌ Nie udało się pobrać emaili: {result['error']}"
            )
            return

        all_emails = result.get("emails", [])

        cutoff_date      = (datetime.now() - timedelta(days=3)).date()
        today_emails_raw = []
        recent_emails    = []
        for em in all_emails:
            try:
                em_date = parsedate_to_datetime(em["date"]).date()
                if em_date == today_date:
                    today_emails_raw.append(em)
                elif em_date >= cutoff_date:
                    recent_emails.append(em)
            except Exception:
                pass

        today_emails     = [e for e in today_emails_raw if not e.get("is_newsletter")]
        newsletter_count = len(today_emails_raw) - len(today_emails)

        email_config = get_user_email_config(DANIEL_USER_ID)
        all_recent   = today_emails + [e for e in recent_emails if not e.get("is_newsletter")]
        unreplied    = find_unreplied_emails(email_config, all_recent, days_back=3) if email_config else []
        unreplied_map = {_normalize_subject(e['subject']): e for e in unreplied}

        if not today_emails:
            no_email_msg = f"📧 *Email Summary - {today_str}*\n\n✅ Brak nowych ważnych emaili dzisiaj."
            if newsletter_count:
                no_email_msg += f"\n_(pominięto {newsletter_count} newsletterów/mailingów)_"
            if unreplied:
                no_email_msg += f"\n\n🚨 *UWAGA: {len(unreplied)} emaili bez odpowiedzi z ostatnich 3 dni!*\n"
                for em in unreplied[:5]:
                    days = em.get('days_waiting', '?')
                    no_email_msg += f"  • *{em['subject']}* — od: {em['from']} _(czeka {days}d)_\n"
            _ctx.app.client.chat_postMessage(channel=dm_ch, text=no_email_msg)
            logger.info("✅ Email Summary wysłany (brak ważnych emaili).")
            return

        emails_for_claude = "\n\n".join([
            f"Email {i+1}:\nOd: {e['from']}\nTemat: {e['subject']}\nPodgląd: {e['body_preview']}"
            for i, e in enumerate(today_emails)
        ])

        claude_prompt = f"""Filtrujesz skrzynkę Daniela Koszuka, właściciela agencji marketingowej Pato.

Newslettery zostały już odfiltrowane. Spośród {len(today_emails)} emaili wyciągnij TYLKO te które są naprawdę istotne.

IMPORTANT — email trafia tutaj TYLKO gdy:
- Znany klient, partner lub dostawca pisze bezpośrednio do Daniela
- Faktura, płatność lub umowa wymagająca uwagi
- Pytanie lub sprawa która czeka na osobistą odpowiedź Daniela
- Reklamacja lub pilna sprawa od realnej osoby

POMIŃ (oznacz jako SKIP) wszystko inne, w szczególności:
- Formularze kontaktowe ze strony www ("nowe zapytanie", "kontakt ze strony", "formularz")
- Cold sales / outreach — nieznane firmy lub osoby oferujące swoje usługi, "chciałbym przedstawić", "mamy dla Ciebie propozycję", "szukamy partnerów"
- Automatyczne powiadomienia systemowe, potwierdzenia, alerty platform
- Faktury lub raporty które tylko informują, nie wymagają działania
- Ogłoszenia, eventy, webinary, zaproszenia do konferencji

Dla każdego IMPORTANT napisz 1 zdanie po polsku: kto pisze i czego konkretnie potrzebuje.

Emaile:
{emails_for_claude}

Odpowiedz TYLKO w formacie JSON:
{{
  "important": [
    {{"index": 0, "from": "Jan Kowalski <jan@firma.pl>", "subject": "Wycena kampanii Q2", "summary": "Klient prosi o wycenę kampanii na Q2, deadline odpowiedzi do piątku."}}
  ]
}}"""

        claude_response = None
        for _attempt in range(3):
            try:
                claude_response = _ctx.claude.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1500,
                    messages=[{"role": "user", "content": claude_prompt}]
                )
                break
            except Exception as _api_err:
                err_str = str(_api_err)
                if _attempt < 2 and ("529" in err_str or "overloaded" in err_str.lower()):
                    _wait = 40 * (2 ** _attempt)
                    logger.warning(f"⚠️ Claude API overloaded (próba {_attempt+1}/3) — czekam {_wait}s...")
                    _time.sleep(_wait)
                else:
                    raise
        if claude_response is None:
            raise Exception("Claude API niedostępne po 3 próbach")

        raw_text   = claude_response.content[0].text
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        parsed     = json.loads(json_match.group()) if json_match else {"important": []}

        important = parsed.get("important", [])

        for em in important:
            subj = _normalize_subject(em.get("subject", ""))
            if subj in unreplied_map:
                em["unreplied"]    = True
                em["days_waiting"] = unreplied_map[subj].get("days_waiting", 0)

        old_unreplied = [e for e in unreplied if e.get('days_waiting', 0) > 0]

        msg = f"📧 *Emaile - {today_str}*\n"

        if old_unreplied:
            msg += f"\n⏰ *Czekają na odpowiedź:*\n"
            for em in old_unreplied[:5]:
                days = em.get('days_waiting', '?')
                msg += f"• *{em['subject']}* — {em['from']} _(+{days}d)_\n"

        if important:
            msg += f"\n📬 *Dzisiaj ({len(important)}):*\n"
            for em in important:
                idx     = em.get("index", 0)
                raw     = today_emails[idx] if idx < len(today_emails) else {}
                sender  = em.get("from", raw.get("from", "?"))
                subject = em.get("subject", raw.get("subject", "?"))
                summary = em.get("summary", "")
                wait_flag = f" ⏰ _{em['days_waiting']}d bez odp._" if em.get("unreplied") else ""
                msg += f"• *{subject}*{wait_flag}\n"
                msg += f"  {sender}\n"
                if summary:
                    msg += f"  _{summary}_\n"
        else:
            msg += "\n✅ *Brak istotnych emaili dzisiaj*\n"
            if newsletter_count:
                msg += f"_(pominięto {newsletter_count} newsletterów/spamu)_\n"

        _ctx.app.client.chat_postMessage(channel=dm_ch, text=msg)
        logger.info(f"✅ Email Summary wysłany! ({len(today_emails)} emaili, {len(important)} ważnych)")

    except Exception as e:
        logger.error(f"❌ Błąd daily_email_summary_slack: {e}")
        try:
            _ctx.app.client.chat_postMessage(
                channel=_daniel_dm_channel(),
                text=f"📧 **Email Summary - {today_str}**\n\n❌ Błąd generowania podsumowania: {str(e)}"
            )
        except Exception:
            pass
