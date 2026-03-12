import os
import re
import time
import logging
import pytz
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from anthropic import Anthropic

# ── shared state (must be imported before job modules) ────────────────────────
import _ctx

# ── config ────────────────────────────────────────────────────────────────────
from config.constants import (
    TEAM_MEMBERS, CHANNEL_CLIENT_MAP,
    EMPLOYEE_MSG_KEYWORDS, REQUEST_CATEGORY_LABELS,
)

# ── tools ─────────────────────────────────────────────────────────────────────
from tools.meta_ads import meta_ads_tool
from tools.google_ads import google_ads_tool
from tools.google_analytics import google_analytics_tool
from tools.email_tools import email_tool, get_user_email_config
from tools.slack_tools import slack_read_channel_tool, slack_read_thread_tool

# ── jobs ──────────────────────────────────────────────────────────────────────
from jobs.performance_analysis import _dispatch_ads_command
from jobs.daily_digest import generate_daily_digest_dre, daily_digest_dre, weekly_learnings_dre
from jobs.budget_alerts import check_budget_alerts
from jobs.weekly_reports import weekly_report_dre, send_weekly_reports
from jobs.checkin import weekly_checkin, send_checkin_reminders, checkin_summary
from jobs.team import (
    close_request, get_pending_requests, _format_requests_list,
    _next_workday, get_availability_for_date, _format_availability_summary,
    handle_employee_dm, send_daily_team_availability,
    sync_availability_from_slack, remove_availability_entries, find_team_member,
)
from jobs.email_summary import daily_email_summary_slack
from jobs.standup import (
    send_standup_questions, post_standup_summary, handle_standup_reply, handle_standup_slash,
)
from jobs.onboarding import (
    _handle_onboarding_done, check_stale_onboardings, handle_onboard_slash,
)
from jobs.industry_news import weekly_industry_news
from tools.campaign_creator import (
    download_slack_files, upload_creative_to_meta, parse_campaign_request,
    build_meta_targeting, create_campaign_draft, generate_campaign_preview,
    approve_and_launch_campaign, cancel_campaign_draft, validate_campaign_params,
    get_meta_account_id, generate_campaign_expert_analysis,
)
from tools.voice_transcription import transcribe_slack_audio, SLACK_AUDIO_MIMES
from tools.icloud_calendar import icloud_calendar_tool
from tools.memory import init_memory, remember, recall_as_context, get_history

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── initialization ────────────────────────────────────────────────────────────
_ctx.app    = App(token=os.environ.get("SLACK_BOT_TOKEN"))
_ctx.claude = Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))
init_memory()

app       = _ctx.app       # local alias for @app.event / @app.command decorators
anthropic = _ctx.claude    # local alias for handle_mention / handle_message_events


# ── conversation history ──────────────────────────────────────────────────────

def get_conversation_history(user_id):
    if user_id not in _ctx.conversation_history:
        _ctx.conversation_history[user_id] = []
    return _ctx.conversation_history[user_id]


def save_message_to_history(user_id, role, content):
    history = get_conversation_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > 20:
        _ctx.conversation_history[user_id] = history[-20:]


# ── DM helpers (used in handle_mention + handle_message_events) ───────────────

def _resolve_team_member(name_query):
    """Dopasowuje imię (w różnych formach fleksyjnych) do TEAM_MEMBERS."""
    q = name_query.lower().strip()
    if not q:
        return None
    for member in TEAM_MEMBERS:
        aliases_lower = [a.lower() for a in member.get("aliases", [])]
        if q in aliases_lower:
            return member
    for member in TEAM_MEMBERS:
        aliases_lower = [a.lower() for a in member.get("aliases", [])]
        for alias in aliases_lower:
            if alias.startswith(q) or q.startswith(alias):
                return member
    return None


def _parse_send_dm_commands(text):
    """Parsuje 'napisz do X: treść [o HH:MM]' — obsługuje wiele naraz."""
    results = []
    parts = re.split(r'\bnapisz\s+do\b', text, flags=re.IGNORECASE)
    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue
        time_m   = re.search(r'\bo\s+(\d{1,2}:\d{2})\s*$', part, re.IGNORECASE)
        time_str = time_m.group(1) if time_m else None
        if time_m:
            part = part[:time_m.start()].strip()
        part = re.sub(r'\s+i\s*$', '', part, flags=re.IGNORECASE).strip()
        colon_m = re.match(r'(\w+)\s*[:\-]\s*(.*)', part, re.DOTALL)
        if colon_m:
            name    = colon_m.group(1)
            message = colon_m.group(2).strip()
        else:
            words = part.split(None, 1)
            if len(words) < 2:
                continue
            name, message = words[0], words[1].strip()
        if message:
            results.append({"name": name, "message": message, "time": time_str})
    return results


def _parse_schedule_time(time_str):
    """Konwertuje 'HH:MM' na Unix timestamp (dziś lub jutro jeśli już minęło)."""
    h, m   = map(int, time_str.split(":"))
    tz     = pytz.timezone("Europe/Warsaw")
    now    = datetime.now(tz)
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int(target.timestamp())


# ── daily summaries (16:00 — podsumowanie kanałów przez Claude) ───────────────

def daily_summaries():
    warsaw_tz   = pytz.timezone('Europe/Warsaw')
    today       = datetime.now(warsaw_tz)
    start_of_day = today.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        result   = app.client.conversations_list(types="public_channel,private_channel")
        channels = result["channels"]

        for channel in channels:
            if channel.get("is_member"):
                channel_id   = channel["id"]
                channel_name = channel["name"]

                messages_result = app.client.conversations_history(
                    channel=channel_id,
                    oldest=str(int(start_of_day.timestamp()))
                )
                messages = messages_result.get("messages", [])

                if len(messages) >= 3:
                    messages_text = "\n".join([
                        f"{msg.get('user', 'Unknown')}: {msg.get('text', '')}"
                        for msg in reversed(messages[:50])
                    ])

                    summary = anthropic.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=300,
                        messages=[{
                            "role": "user",
                            "content": (
                                f"Na podstawie dzisiejszych wiadomości z kanału #{channel_name} napisz BARDZO krótkie podsumowanie (max 2 zdania ogólnie co się działo). "
                                f"Następnie jeśli były jakieś problemy, alerty, błędy lub rzeczy wymagające uwagi — wylistuj je osobno jako '*Wymaga uwagi:*'. "
                                f"Jeśli nie było nic alarmującego, nie pisz tej sekcji w ogóle. Nie opisuj każdej kampanii z osobna.\n\n{messages_text}"
                            )
                        }]
                    )

                    summary_text = summary.content[0].text
                    app.client.chat_postMessage(
                        channel=channel_id,
                        text=f"📋 *Podsumowanie dnia — {today.strftime('%d.%m.%Y')}*\n\n{summary_text}"
                    )

    except Exception as e:
        logger.error(f"Błąd podczas tworzenia podsumowań: {e}")


# ── campaign questionnaire helper ────────────────────────────────────────────

def _check_missing_campaign_fields(params: dict, files: list) -> list:
    """Zwraca listę pytań o brakujące wymagane pola kampanii.
    Returns: [] jeśli wszystko OK, inaczej lista stringów z pytaniami."""
    qs = []
    if not params.get("client_name"):
        qs.append("*Klient* — dla kogo kampania? (`dre` / `instax` / `m2` / `pato`)")
    if not params.get("daily_budget"):
        qs.append("*Budżet dzienny* — ile PLN/dzień? (np. `50 zł`)")
    if params.get("link_enabled", True) and not params.get("website_url"):
        qs.append("*Link URL* — jaki adres strony? (np. `https://dre.pl`) lub napisz `bez linku`")
    return qs


def _merge_pending_campaign_params(pending_params: dict, new_params: dict, user_message: str) -> dict:
    """Uzupełnia brakujące pola w pending_params danymi z new_params i wiadomości."""
    pp = pending_params
    _ua_lower = user_message.lower()

    # Wymagane pola — tylko uzupełniamy jeśli brakuje
    if not pp.get("client_name") and new_params.get("client_name"):
        pp["client_name"] = new_params["client_name"]
    if not pp.get("daily_budget") and new_params.get("daily_budget"):
        pp["daily_budget"] = float(new_params["daily_budget"])

    # Link / bez linku
    if any(k in _ua_lower for k in ("bez linku", "no link", "bez url", "bez linka")):
        pp["link_enabled"] = False
        pp["website_url"] = None
    elif not pp.get("website_url") and new_params.get("website_url"):
        pp["website_url"] = new_params["website_url"]

    # Targeting — uzupełniamy z new_params tylko niedefaultowe wartości
    _nt = new_params.get("targeting") or {}
    _ot = pp.get("targeting") or {
        "gender": "all", "age_min": 18, "age_max": 65,
        "locations": ["Polska"], "interests": [],
    }
    if _nt.get("gender") and _nt["gender"] != "all":
        _ot["gender"] = _nt["gender"]
    if _nt.get("locations"):
        _ot["locations"] = _nt["locations"]
    if _nt.get("interests"):
        _ot["interests"] = _nt["interests"]
    # Age range z regex z wiadomości (np. "18-32")
    _age_m = re.search(r'(\d{1,2})\s*[-–]\s*(\d{1,2})', user_message)
    if _age_m:
        _ot["age_min"] = int(_age_m.group(1))
        _ot["age_max"] = int(_age_m.group(2))
    pp["targeting"] = _ot

    return pp


_GROUP_CHAT_RULES = (
    "Jesteś w grupowym czacie z kilkoma osobami z teamu. Zasady:\n"
    "- Zachowuj się jak uczestnik rozmowy, nie jak bot który się prezentuje\n"
    '- NIE wypisuj swoich możliwości, NIE zaczynaj od "mogę pomóc w..." \u2014 po prostu odpowiadaj\n'
    "- Czytaj historię czatu (podaną wyżej) żeby rozumieć kontekst rozmowy\n"
    "- Odpowiadaj naturalnie i bezpośrednio na to co jest pytane lub omawiane\n"
    "- Krótko gdy wystarczy; szczegółowo gdy ktoś prosi o analizę lub dane\n"
    "- Gdy pytają o kampanie/dane \u2014 wywołaj narzędzie i daj konkretne liczby"
)


# ── app_mention handler ───────────────────────────────────────────────────────

@app.event("app_mention")
def handle_mention(event, say):
    user_message = event['text']
    user_message = re.sub(r'<@[A-Z0-9]+>', '', user_message).strip()  # Usuń wszystkie wzmianki bota

    msg_lower_m = user_message.lower()

    # === ONBOARDING: @Sebol done N w wątku onboardingowym ===
    if re.search(r'\bdone\b', msg_lower_m):
        if _handle_onboarding_done(event, say):
            return

    # === ADS COMMANDS: "ads health", "ads anomalies dre" itp. ===
    _ads_match = re.search(
        r'\bads\s+(health|anomalies|anomalie|pacing|winners|losers)\b(.*)',
        msg_lower_m
    )
    if _ads_match:
        _dispatch_ads_command(
            _ads_match.group(1).strip(),
            event.get("channel", ""),
            _ads_match.group(2).strip(),
            say,
        )
        return

    # === "zamknij #N" — Daniel zamyka prośbę ===
    close_match = re.search(r'zamknij\s+#?(\d+)', msg_lower_m)
    if close_match:
        req_id = int(close_match.group(1))
        closed = close_request(req_id)
        if closed:
            cat_label = REQUEST_CATEGORY_LABELS.get(closed.get("category", "inne"), "📌 Inne")
            say(f"✅ Prośba *#{req_id}* zamknięta!\n"
                f"_{closed['user_name']}_ — {cat_label}: {closed['summary']}")
        else:
            say(f"❌ Nie znalazłem otwartej prośby *#{req_id}*.")
        return

    # === "usuń nieobecność X" / "resetuj nieobecności X" ===
    # Używamy substring check zamiast regex — Slack może zwracać polskie znaki w NFD lub NFC
    _msg_flat = (msg_lower_m
                 .replace('ń', 'n').replace('ś', 's').replace('ć', 'c')
                 .replace('ż', 'z').replace('ź', 'z').replace('ą', 'a')
                 .replace('ę', 'e').replace('ó', 'o').replace('ł', 'l'))
    _is_rm_abs = (
        ('usun nieobecno' in _msg_flat or 'usuń nieobecno' in msg_lower_m) or
        ('resetuj nieobecno' in _msg_flat) or
        ('wyczys nieobecno' in _msg_flat or 'wyczysc nieobecno' in _msg_flat)
    )
    if _is_rm_abs:
        # wyciągnij imię — wszystko po słowie kluczowym
        for _kw in ['nieobecnosc', 'nieobecnosci', 'nieobecność', 'nieobecności']:
            if _kw in _msg_flat or _kw in msg_lower_m:
                _after = (msg_lower_m.split(_kw, 1) + [''])[1].strip()
                if not _after:
                    _after = (_msg_flat.split(_kw.replace('ś','s').replace('ć','c'), 1) + [''])[1].strip()
                break
        else:
            _after = ''
        _rm_member = None
        for _w in _after.split():
            _rm_member = find_team_member(_w)
            if _rm_member:
                break
        if _rm_member:
            _removed = remove_availability_entries(_rm_member["slack_id"])
            say(f"🗑️ Usunąłem *{_removed}* wpisów nieobecności dla *{_rm_member['name']}*.")
        else:
            say("❌ Nie rozpoznałem imienia. Napisz np. `usuń nieobecność Piotrka`.")
        return

    # === "co czeka?" / "prośby" — lista otwartych próśb ===
    if any(t in msg_lower_m for t in ["co czeka", "prośby", "prosby", "otwarte prośby",
                                       "pending", "co jest otwarte", "lista próśb"]):
        pending = get_pending_requests()
        say(_format_requests_list(pending))
        return

    # === AVAILABILITY QUERY: "kto jutro?" / "dostępność" ===
    if any(t in msg_lower_m for t in ["kto jutro", "kto nie będzie", "kto nie bedzie",
                                       "dostępność", "dostepnosc", "nieobecności", "nieobecnosci",
                                       "kto jest jutro", "availability"]):
        if "pojutrze" in msg_lower_m:
            target = _next_workday(_next_workday())
        else:
            target = _next_workday()
        target_str   = target.strftime('%Y-%m-%d')
        target_label = target.strftime('%A %d.%m.%Y')
        entries = get_availability_for_date(target_str)
        say(_format_availability_summary(entries, target_label))
        return

    # === NAPISZ DO: "napisz do Magdy: ..." / "napisz do Emki: ... o 15:00" ===
    if re.search(r'\bnapisz\s+do\b', msg_lower_m):
        _dm_commands = _parse_send_dm_commands(user_message)
        if _dm_commands:
            _dm_results = []
            for _cmd in _dm_commands:
                _member = _resolve_team_member(_cmd["name"])
                if not _member:
                    _dm_results.append(f"❌ Nie znam osoby *{_cmd['name']}*")
                    continue
                if _cmd["time"]:
                    _ts = _parse_schedule_time(_cmd["time"])
                    try:
                        _dm_ch = app.client.conversations_open(users=_member["slack_id"])["channel"]["id"]
                        app.client.chat_scheduleMessage(
                            channel=_dm_ch,
                            text=_cmd["message"],
                            post_at=_ts,
                        )
                        _dm_results.append(f"✅ Zaplanowano do *{_member['name']}* o {_cmd['time']}: _{_cmd['message']}_")
                    except Exception as _e:
                        _dm_results.append(f"❌ Błąd planowania do {_member['name']}: {_e}")
                else:
                    try:
                        _dm_ch = app.client.conversations_open(users=_member["slack_id"])["channel"]["id"]
                        app.client.chat_postMessage(
                            channel=_dm_ch,
                            text=_cmd["message"],
                        )
                        _dm_results.append(f"✅ Wysłano do *{_member['name']}*: _{_cmd['message']}_")
                    except Exception as _e:
                        _dm_results.append(f"❌ Błąd wysyłania do {_member['name']}: {_e}")
            say("\n".join(_dm_results))
            return

    # Email trigger — wyniki zawsze na DM, nie w kanale
    if any(t in user_message.lower() for t in ["test email", "email test", "email summary"]):
        say("📧 Uruchamiam Email Summary... wyślę Ci to na DM.")
        try:
            email_config = get_user_email_config("UTE1RN6SJ")
            if not email_config:
                say("❌ Brak konfiguracji email (`EMAIL_ACCOUNTS`).")
                return
            daily_email_summary_slack()
        except Exception as e:
            say(f"❌ Błąd Email Summary: `{str(e)}`")
            logger.error(f"Błąd email trigger w mention: {e}")
        return

    # === NIEOBECNOŚCI / PROŚBY via @mention ===
    _mention_uid = event.get('user', '')
    if _mention_uid and any(kw in msg_lower_m for kw in EMPLOYEE_MSG_KEYWORDS):
        _mention_name = next(
            (m['name'] for m in TEAM_MEMBERS if m['slack_id'] == _mention_uid), None
        )
        if not _mention_name:
            try:
                _ui = app.client.users_info(user=_mention_uid)
                _mention_name = (
                    _ui['user'].get('real_name')
                    or _ui['user'].get('profile', {}).get('display_name')
                    or _ui['user'].get('name', _mention_uid)
                )
            except Exception:
                _mention_name = _mention_uid
        logger.info(f"MENTION ABSENCE CHECK → uid={_mention_uid} name={_mention_name!r}")
        if handle_employee_dm(_mention_uid, _mention_name, user_message, say):
            return

    channel   = event['channel']
    thread_ts = event.get('thread_ts', event['ts'])
    _mention_user_id = event.get('user', '')

    # Track thread so bot responds to follow-ups without explicit mention
    _ctx.bot_threads.add((channel, thread_ts))

    # === CAMPAIGN: zatwierdź kampanię {id} ===
    _approve_m = re.search(r'(zatwierdź|zatwierdz|uruchom)\s+kampanię\s+(\d+)', msg_lower_m)
    if _approve_m:
        _camp_id = _approve_m.group(2)
        say(text=f"🚀 Uruchamiam kampanię `{_camp_id}`...", thread_ts=thread_ts)
        say(text=approve_and_launch_campaign(_camp_id), thread_ts=thread_ts)
        return

    # === CAMPAIGN: anuluj kampanię {id} ===
    _cancel_m = re.search(r'(anuluj|usuń|usun|skasuj)\s+kampanię\s+(\d+)', msg_lower_m)
    if _cancel_m:
        _camp_id = _cancel_m.group(2)
        say(text=cancel_campaign_draft(_camp_id), thread_ts=thread_ts)
        return

    # === CAMPAIGN CREATION: wyłączone — używaj /kampania ===
    # Stary keyword trigger usunięty. Kampanie tworzone tylko przez /kampania.

    channel_type  = event.get('channel_type', 'channel')
    is_group_chat = channel_type in ('channel', 'group', 'mpim')

    channel_history_ctx = ""
    if is_group_chat:
        try:
            hist_res  = app.client.conversations_history(channel=channel, limit=15)
            raw_msgs  = hist_res.get('messages', [])[::-1]
            name_map  = {m['slack_id']: m['name'] for m in TEAM_MEMBERS}
            lines = []
            for m in raw_msgs:
                if m.get('ts') == event['ts']:
                    continue
                uid  = m.get('user', '')
                name = name_map.get(uid, 'Bot' if not uid else uid)
                text = (m.get('text') or '').strip()
                if text:
                    lines.append(f"{name}: {text}")
            if lines:
                channel_history_ctx = (
                    "[Ostatnie wiadomości w tym czacie — czytaj jako kontekst rozmowy:]\n"
                    + "\n".join(lines) + "\n"
                )
        except Exception as e:
            logger.error(f"Błąd pobierania historii kanału: {e}")

    today           = datetime.now()
    today_formatted = today.strftime('%d %B %Y')
    today_iso       = today.strftime('%Y-%m-%d')

    _sender_uid  = event.get('user', '')
    _sender_name = next((m['name'] for m in TEAM_MEMBERS if m['slack_id'] == _sender_uid), None)
    if not _sender_name and _sender_uid:
        try:
            _ui = app.client.users_info(user=_sender_uid)
            _sender_name = _ui['user'].get('real_name') or _ui['user'].get('name') or _sender_uid
        except Exception:
            _sender_name = _sender_uid
    _sender_line = f"\n# NADAWCA\nTa wiadomość pochodzi od: *{_sender_name}* (Slack ID: {_sender_uid}). Zwracaj się do niego po imieniu." if _sender_name else ""

    SYSTEM_PROMPT = f"""
# DATA
Dzisiaj: {today_formatted} ({today_iso}). Pytania o "styczeń 2026" czy wcześniej = PRZESZŁOŚĆ, masz dane!{_sender_line}

# KIM JESTEŚ
Sebol — asystent agencji marketingowej Pato. Pomagasz w WSZYSTKIM co dotyczy codziennej pracy agencji: analiza kampanii, organizacja teamu, emaile, raporty, pytania, decyzje. Jesteś częścią teamu — nie jesteś tylko narzędziem do raportów.

# CO POTRAFISZ (lista funkcji gdy ktoś pyta lub się wita)
📊 *Kampanie* — analizujesz Meta Ads i Google Ads w czasie rzeczywistym (CTR, ROAS, spend, konwersje, alerty)
📧 *Emaile* — codzienne podsumowanie ważnych emaili Daniela o 16:00 (+ na żądanie: "test email")
📅 *Kalendarz* — masz dostęp do kalendarza iCloud Daniela: sprawdzasz plan dnia/tygodnia, dodajesz spotkania
👥 *Team* — pracownicy zgłaszają nieobecności i prośby przez DM, Ty zbierasz i raportujesz Danielowi o 17:00 na #zarzondpato
📋 *Prośby* — zapisujesz prośby teamu (#ID), Daniel zamyka je przez "@Sebol zamknij #N"
⛔ ZAKAZ: NIE zapisuj kampanii reklamowych jako "prośby" (#ID). Kampanie tworzysz bezpośrednio — pytaj o brakujące dane i buduj. Prośby (#ID) to TYLKO: urlopy, zakupy, dostępy, spotkania — sprawy wymagające decyzji szefa.
🧠 *Daily Digest* — codziennie o 9:00 raport DRE z benchmarkami i smart rekomendacjami
📈 *Weekly Learnings* — co poniedziałek i czwartek o 8:30 analiza wzorców kampanii
⚡ *Alerty budżetowe* — pilnujesz żeby kampanie nie przebijały budżetu
🎤 *Głosówki* — rozumiesz wiadomości głosowe ze Slacka (transkrybuję je automatycznie)
💬 *Ogólna pomoc* — pytania, drafty, pomysły, wszystko co potrzebuje zespół

# GDY KTOŚ SIĘ WITA / PYTA CO UMIESZ
Przedstaw się krótko i naturalnie. Wymień funkcje w formie listy jak powyżej. NIE mów że "jesteś gotowy do analizy kampanii" — jesteś multi-taskerem, nie tylko narzędziem do raportów.

# KLIENCI
META ADS: "instax"/"fuji" → Instax Fujifilm | "zbiorcze" → Kampanie zbiorcze | "drzwi dre" → DRE (drzwi)
GOOGLE ADS: "3wm"/"pato" → Agencja | "dre 2024"/"dre24" → DRE 2024 | "dre 2025"/"dre25"/"dre" → DRE 2025 | "m2" → M2 (nieruchomości) | "zbiorcze" → Zbiorcze
⚠️ "dre" = producent drzwi, NIE raper!

# NARZĘDZIA - ZAWSZE UŻYWAJ NAJPIERW
Pytanie o kampanie/metryki/spend/ROAS/CTR → WYWOŁAJ narzędzie:
- get_meta_ads_data() → Facebook/Instagram
- get_google_ads_data() → Google Ads (kampanie, kliknięcia, wydatki, ROAS, CTR, CPC, reklamy)
- get_ga4_data() → Google Analytics 4 / GA4 / analytics (ruch na stronie, sesje, użytkownicy, źródła ruchu, bounce rate) - NIE Google Ads!
- manage_calendar() → kalendarz iCloud: "co mam jutro", "plan na tydzień", "dodaj spotkanie" → ZAWSZE wywołaj to narzędzie, nie mów że nie masz dostępu!
NIGDY nie mów "nie mam dostępu" - zawsze najpierw użyj narzędzi!
⛔ BEZWZGLĘDNY ZAKAZ: Gdy ktoś pyta o GA4/analytics → wywołaj get_ga4_data() i podaj TYLKO dane z tego narzędzia. NIGDY nie zastępuj danych GA4 estymacjami z Meta Ads, Google Ads ani żadnych innych źródeł. Jeśli get_ga4_data() zwróci błąd → powiedz wprost jaki błąd wystąpił, NIE wymyślaj alternatywnych danych.

# TON I STYL
- Polski, naturalny, mówisz "Ty", jesteś częścią teamu
- Konkretne liczby: "CTR 2.3%" nie "niski CTR"
- Emoji: 🔴 🟡 🟢 📊 💰 🚀 ⚠️ ✅
- Direct, asertywny, actionable - unikaj ogólników i korporomowy
- Krytykujesz kampanie, nie ludzi

# RED FLAGS (kampanie)
🔴 CRITICAL: ROAS <2.0 | CTR <0.5% | Budget pace >150% | Zero conversions 3+ dni
🟡 WARNING: ROAS 2.0-2.5 | CTR <1% | CPC +30% d/d | Frequency >4 | Pace >120%

# BENCHMARKI
Meta e-com: CTR 1.5-2.5% (>3% excel) | CPC 3-8 PLN | ROAS >3.0 | Freq <3 ok, >5 fatigue
Google Search: CTR 2-5% | CPC 2-10 PLN | ROAS >4.0
Lead gen: CTR 1-2% | CVR landing page >3%

# STRUKTURA ODPOWIEDZI
Alert → 🔴 Problem | Metryki | Impact | Root cause | Akcje (1-3 kroki z timeframe)
Analiza → SPEND | PERFORMANCE (ROAS/Conv/CTR) | 🔥 Top performer | ⚠️ Needs attention | 💡 Next steps
Pytanie → Direct answer → Context → Actionable next step

{"# TRYB: GRUPOWY CZAT" if is_group_chat else ""}
{_GROUP_CHAT_RULES if is_group_chat else ""}
"""

    tools = [
        {
            "name": "get_meta_ads_data",
            "description": "Pobiera szczegółowe statystyki z Meta Ads (Facebook Ads) na poziomie kampanii, ad setów lub pojedynczych reklam. Obsługuje breakdowny demograficzne i placement. Użyj gdy użytkownik pyta o kampanie, ad sety, reklamy, wydatki, wyniki, konwersje, ROAS, demografię (wiek/płeć/kraj) lub placement (Instagram/Facebook/Stories).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "client_name": {
                        "type": "string",
                        "description": "Nazwa klienta/biznesu. WYMAGANE. Dostępne: 'instax', 'fuji', 'instax/fuji', 'zbiorcze', 'kampanie zbiorcze', 'drzwi dre'. Wyciągnij z pytania użytkownika (np. 'jak kampanie dla instax?' → client_name='instax'). Jeśli użytkownik nie poda - zapytaj."
                    },
                    "date_from": {"type": "string", "description": "Data początkowa. Format: YYYY-MM-DD lub względnie ('wczoraj', 'ostatni tydzień', 'ostatni miesiąc', '7 dni temu')."},
                    "date_to":   {"type": "string", "description": "Data końcowa. Format: YYYY-MM-DD lub 'dzisiaj'. Domyślnie dzisiaj."},
                    "level":     {"type": "string", "enum": ["campaign", "adset", "ad"], "description": "Poziom danych: 'campaign' (kampanie), 'adset' (zestawy reklam), 'ad' (pojedyncze reklamy). Domyślnie 'campaign'."},
                    "campaign_name": {"type": "string", "description": "Filtr po nazwie kampanii (częściowa nazwa działa)."},
                    "adset_name":    {"type": "string", "description": "Filtr po nazwie ad setu (częściowa nazwa działa)."},
                    "ad_name":       {"type": "string", "description": "Filtr po nazwie reklamy (częściowa nazwa działa)."},
                    "metrics":       {"type": "array", "items": {"type": "string"}, "description": "Lista metryk: campaign_name, adset_name, ad_name, spend, impressions, clicks, ctr, cpc, cpm, reach, frequency, conversions, cost_per_conversion, purchase_roas, actions, action_values, budget_remaining, inline_link_clicks, inline_link_click_ctr"},
                    "breakdown":     {"type": "string", "description": "Breakdown dla demografii/placement: 'age' (wiek), 'gender' (płeć), 'country' (kraj), 'placement' (miejsce wyświetlenia), 'device_platform' (urządzenie). Może być też lista np. ['age', 'gender']"},
                    "limit":         {"type": "integer", "description": "Limit wyników (max liczba kampanii/adsetów/reklam do zwrócenia)."}
                },
                "required": []
            }
        },
        {
            "name": "manage_email",
            "description": "Zarządza emailami użytkownika - czyta, wysyła i wyszukuje wiadomości. Użyj gdy użytkownik pyta o emaile, chce wysłać wiadomość lub szuka czegoś w skrzynce.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action":  {"type": "string", "enum": ["read", "send", "search"], "description": "Akcja: 'read' = odczytaj najnowsze emaile, 'send' = wyślij email, 'search' = szukaj emaili po frazie"},
                    "limit":   {"type": "integer", "description": "Ile emaili pobrać/przeszukać (domyślnie 10)"},
                    "to":      {"type": "string", "description": "Adres odbiorcy (tylko dla action='send')"},
                    "subject": {"type": "string", "description": "Temat emaila (tylko dla action='send')"},
                    "body":    {"type": "string", "description": "Treść emaila (tylko dla action='send')"},
                    "query":   {"type": "string", "description": "Fraza do wyszukania (tylko dla action='search')"}
                },
                "required": ["action"]
            }
        },
        {
            "name": "get_google_ads_data",
            "description": "Pobiera szczegółowe statystyki z Google Ads na poziomie kampanii, ad groups lub pojedynczych reklam. Użyj gdy użytkownik pyta o kampanie Google, wydatki w Google Ads, wyniki wyszukiwania, kampanie displayowe.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "client_name":  {"type": "string", "description": "Nazwa klienta/biznesu. WYMAGANE. Dostępne: '3wm', 'pato', 'dre 2024', 'dre24', 'dre 2025', 'dre25', 'dre', 'm2', 'zbiorcze'. Wyciągnij z pytania użytkownika."},
                    "date_from":    {"type": "string", "description": "Data początkowa. Format: YYYY-MM-DD lub względnie ('wczoraj', 'ostatni tydzień')."},
                    "date_to":      {"type": "string", "description": "Data końcowa. Format: YYYY-MM-DD lub 'dzisiaj'. Domyślnie dzisiaj."},
                    "level":        {"type": "string", "enum": ["campaign", "adgroup", "ad"], "description": "Poziom danych: 'campaign' (kampanie), 'adgroup' (grupy reklam), 'ad' (pojedyncze reklamy). Domyślnie 'campaign'."},
                    "campaign_name": {"type": "string", "description": "Filtr po nazwie kampanii."},
                    "adgroup_name":  {"type": "string", "description": "Filtr po nazwie ad group."},
                    "ad_name":       {"type": "string", "description": "Filtr po nazwie reklamy."},
                    "metrics":       {"type": "array", "items": {"type": "string"}, "description": "Lista metryk: campaign.name, ad_group.name, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.ctr, metrics.average_cpc"},
                    "limit":         {"type": "integer", "description": "Limit wyników."}
                },
                "required": []
            }
        },
        {
            "name": "get_ga4_data",
            "description": "Pobiera dane z Google Analytics 4: sesje, użytkownicy, strony, konwersje, przychody, źródła ruchu. Użyj gdy użytkownik pyta o ruch na stronie, GA4, Google Analytics, sesje, bounce rate, źródła ruchu (organic/paid/direct), konwersje z GA4.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "client_name":  {"type": "string", "description": "Nazwa klienta. WYMAGANE. Musi odpowiadać klientowi z GA4_PROPERTY_IDS."},
                    "date_from":    {"type": "string", "description": "Data początkowa. Format: YYYY-MM-DD, '7daysAgo', '30daysAgo', 'yesterday', lub po polsku: 'ostatni tydzień', 'ostatni miesiąc'."},
                    "date_to":      {"type": "string", "description": "Data końcowa. Format: YYYY-MM-DD lub 'today'. Domyślnie 'today'."},
                    "dimensions":   {"type": "array", "items": {"type": "string"}, "description": "Wymiary GA4, np. ['sessionDefaultChannelGroup', 'sessionSourceMedium', 'pagePath', 'deviceCategory', 'country', 'landingPage']. Domyślnie: sessionDefaultChannelGroup + sessionSourceMedium."},
                    "metrics":      {"type": "array", "items": {"type": "string"}, "description": "Metryki GA4, np. ['sessions', 'totalUsers', 'newUsers', 'screenPageViews', 'bounceRate', 'conversions', 'totalRevenue', 'averageSessionDuration']. Domyślnie wszystkie."},
                    "limit":        {"type": "integer", "description": "Maksymalna liczba wierszy wyników (domyślnie 20)."}
                },
                "required": []
            }
        },
        {
            "name": "slack_read_channel",
            "description": "Czyta historię wiadomości z kanału Slack. Użyj gdy użytkownik pyta o przeszłe wiadomości, chce podsumowanie rozmów, lub analizę konwersacji na kanale.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string", "description": "ID kanału Slack. Jeśli użytkownik mówi 'ten kanał' lub 'tutaj', zostaw PUSTE - bot użyje obecnego kanału automatycznie."},
                    "limit":  {"type": "integer", "description": "Ile wiadomości pobrać (domyślnie 50, max 100)"},
                    "oldest": {"type": "string",  "description": "Data/timestamp od której czytać (format: YYYY-MM-DD lub Unix timestamp)"},
                    "latest": {"type": "string",  "description": "Data/timestamp do której czytać (format: YYYY-MM-DD lub Unix timestamp)"}
                },
                "required": []
            }
        },
        {
            "name": "slack_read_thread",
            "description": "Czyta wątek (thread) z kanału. Użyj gdy użytkownik pyta o odpowiedzi w wątku lub kontynuację rozmowy.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string", "description": "ID kanału"},
                    "thread_ts":  {"type": "string", "description": "Timestamp wiadomości która rozpoczyna wątek"}
                },
                "required": ["channel_id", "thread_ts"]
            }
        },
        {
            "name": "manage_calendar",
            "description": "Zarządza kalendarzem iCloud użytkownika. Użyj gdy pyta o swoje spotkania, plan dnia/tygodnia, chce dodać wydarzenie do kalendarza lub sprawdzić co ma zaplanowane.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action":        {"type": "string", "enum": ["list", "create"], "description": "'list' = pobierz listę wydarzeń, 'create' = utwórz nowe wydarzenie."},
                    "date_from":     {"type": "string", "description": "Data początkowa zakresu (YYYY-MM-DD). Domyślnie dzisiaj."},
                    "date_to":       {"type": "string", "description": "Data końcowa zakresu (YYYY-MM-DD). Domyślnie +7 dni."},
                    "title":         {"type": "string", "description": "Tytuł wydarzenia (wymagane przy action='create')."},
                    "start":         {"type": "string", "description": "Data i godzina startu (YYYY-MM-DD HH:MM, wymagane przy action='create')."},
                    "end":           {"type": "string", "description": "Data i godzina końca (YYYY-MM-DD HH:MM, opcjonalne — domyślnie +1h)."},
                    "location":      {"type": "string", "description": "Miejsce spotkania (opcjonalne)."},
                    "description":   {"type": "string", "description": "Opis/notatka do wydarzenia (opcjonalne)."},
                    "calendar_name": {"type": "string", "description": "Nazwa kalendarza iCloud (opcjonalne — jeśli nie podano, używa pierwszego dostępnego)."},
                },
                "required": ["action"]
            }
        },
    ]

    try:
        user_id = event.get('user')
        history = get_conversation_history(user_id)

        # Store incoming message to long-term memory
        remember(user_id, channel, event.get("ts", ""), "user", user_message)

        contextual_message = (
            (channel_history_ctx + user_message) if channel_history_ctx else user_message
        )
        messages = history + [{"role": "user", "content": contextual_message}]

        while True:
            response = anthropic.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages
            )

            if response.stop_reason == "tool_use":
                tool_use_block = next(b for b in response.content if b.type == "tool_use")
                tool_name  = tool_use_block.name
                tool_input = tool_use_block.input
                logger.info(f"Claude wywołał narzędzie: {tool_name} z parametrami: {tool_input}")

                if tool_name == "get_meta_ads_data":
                    tool_result = meta_ads_tool(
                        date_from=tool_input.get('date_from'),
                        date_to=tool_input.get('date_to'),
                        level=tool_input.get('level', 'campaign'),
                        campaign_name=tool_input.get('campaign_name'),
                        adset_name=tool_input.get('adset_name'),
                        ad_name=tool_input.get('ad_name'),
                        metrics=tool_input.get('metrics'),
                        breakdown=tool_input.get('breakdown'),
                        limit=tool_input.get('limit'),
                        client_name=tool_input.get('client_name')
                    )
                elif tool_name == "manage_email":
                    tool_result = email_tool(
                        user_id=event.get('user'),
                        action=tool_input.get('action'),
                        limit=tool_input.get('limit', 10),
                        to=tool_input.get('to'),
                        subject=tool_input.get('subject'),
                        body=tool_input.get('body'),
                        query=tool_input.get('query')
                    )
                elif tool_name == "get_google_ads_data":
                    tool_result = google_ads_tool(
                        date_from=tool_input.get('date_from'),
                        date_to=tool_input.get('date_to'),
                        level=tool_input.get('level', 'campaign'),
                        campaign_name=tool_input.get('campaign_name'),
                        adgroup_name=tool_input.get('adgroup_name'),
                        ad_name=tool_input.get('ad_name'),
                        metrics=tool_input.get('metrics'),
                        limit=tool_input.get('limit'),
                        client_name=tool_input.get('client_name')
                    )
                elif tool_name == "get_ga4_data":
                    tool_result = google_analytics_tool(
                        client_name=tool_input.get('client_name'),
                        date_from=tool_input.get('date_from'),
                        date_to=tool_input.get('date_to'),
                        dimensions=tool_input.get('dimensions'),
                        metrics=tool_input.get('metrics'),
                        limit=tool_input.get('limit', 20),
                    )
                elif tool_name == "slack_read_channel":
                    tool_result = slack_read_channel_tool(
                        channel_id=tool_input.get('channel_id') or event.get('channel'),
                        limit=tool_input.get('limit', 50),
                        oldest=tool_input.get('oldest'),
                        latest=tool_input.get('latest')
                    )
                elif tool_name == "slack_read_thread":
                    tool_result = slack_read_thread_tool(
                        channel_id=tool_input.get('channel_id'),
                        thread_ts=tool_input.get('thread_ts')
                    )
                elif tool_name == "manage_calendar":
                    _cal_user = event.get('user')
                    _owner_id = os.environ.get("CALENDAR_OWNER_SLACK_ID")
                    if _owner_id and _cal_user != _owner_id:
                        tool_result = {"error": "Brak dostępu — kalendarz jest prywatny."}
                    else:
                        tool_result = icloud_calendar_tool(
                            action=tool_input.get('action', 'list'),
                            date_from=tool_input.get('date_from'),
                            date_to=tool_input.get('date_to'),
                            title=tool_input.get('title'),
                            start=tool_input.get('start'),
                            end=tool_input.get('end'),
                            location=tool_input.get('location'),
                            description=tool_input.get('description'),
                            calendar_name=tool_input.get('calendar_name'),
                        )
                else:
                    tool_result = {"error": "Nieznane narzędzie"}

                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": [{
                        "type":        "tool_result",
                        "tool_use_id": tool_use_block.id,
                        "content":     str(tool_result)
                    }]
                })
                continue

            else:
                response_text = next(
                    (block.text for block in response.content if hasattr(block, "text")),
                    "Przepraszam, nie mogłem wygenerować odpowiedzi."
                )
                save_message_to_history(user_id, "user", user_message)
                save_message_to_history(user_id, "assistant", response_text)
                # Store bot reply to long-term memory
                remember(user_id, channel, event.get("ts", "") + "_bot", "assistant", response_text)

                if is_group_chat and not event.get('thread_ts'):
                    say(text=response_text)
                else:
                    say(text=response_text, thread_ts=thread_ts)
                break

    except Exception as e:
        logger.error(f"Błąd: {e}")
        if is_group_chat and not event.get('thread_ts'):
            say(text=f"Przepraszam, wystąpił błąd: {str(e)}")
        else:
            say(text=f"Przepraszam, wystąpił błąd: {str(e)}", thread_ts=thread_ts)


# ── /ads slash command ────────────────────────────────────────────────────────

@app.command("/ads")
def handle_ads_slash(ack, respond, command):
    ack()
    text       = (command.get("text") or "").strip()
    channel_id = command.get("channel_id", "")
    parts      = text.split(None, 1)
    if not parts:
        known = " | ".join(f"`{k}`" for k in ["health", "anomalies", "pacing", "winners", "losers"])
        respond(f"Użycie: `/ads [komenda] [klient]`\nKomendy: {known}")
        return
    subcmd     = parts[0]
    extra_text = parts[1] if len(parts) > 1 else ""
    _dispatch_ads_command(subcmd, channel_id, extra_text, respond)


# ── /news slash command ───────────────────────────────────────────────────────

def _news_worker(respond):
    from jobs.industry_news import generate_industry_news_digest, MEDIA_CHANNEL_ID
    try:
        digest = generate_industry_news_digest()
        app.client.chat_postMessage(channel=MEDIA_CHANNEL_ID, text=digest)
        respond(f"✅ Digest wysłany na <#{MEDIA_CHANNEL_ID}>!")
    except Exception as e:
        respond(f"❌ Błąd: {e}")

@app.command("/news")
def handle_news_slash(ack, respond, command):
    """Ręczne wyzwolenie tygodniowego digestu nowości branżowych."""
    import threading
    ack()
    respond("⏳ Szukam nowości... To może zająć chwilę.")
    threading.Thread(target=_news_worker, args=(respond,), daemon=True).start()


# ── /cleanup slash command ────────────────────────────────────────────────────

@app.command("/cleanup")
def handle_cleanup_slash(ack, respond, command, client):
    """Usuwa wszystkie wiadomości bota z bieżącego kanału."""
    ack()
    channel_id = command.get("channel_id", "")
    user_id = command.get("user_id", "")
    text = (command.get("text") or "").strip()

    # Opcjonalny argument: liczba dni (domyślnie 30)
    try:
        days = int(text) if text else 30
    except ValueError:
        respond("Użycie: `/cleanup [liczba_dni]` (domyślnie 30)")
        return

    oldest = str(time.time() - days * 86400)

    respond(f"🧹 Szukam wiadomości bota z ostatnich {days} dni... chwilka.")

    deleted = 0
    errors = 0
    cursor = None

    # Pobierz bot_id bota
    try:
        auth_info = client.auth_test()
        bot_id = auth_info.get("bot_id") or auth_info.get("user_id")
    except Exception as e:
        respond(f"❌ Nie udało się pobrać auth info: {e}")
        return

    while True:
        kwargs = {"channel": channel_id, "limit": 200, "oldest": oldest}
        if cursor:
            kwargs["cursor"] = cursor
        try:
            resp = client.conversations_history(**kwargs)
        except Exception as e:
            logger.error(f"cleanup: conversations_history error: {e}")
            break

        messages = resp.get("messages", [])
        for msg in messages:
            is_bot_msg = (
                msg.get("bot_id") == bot_id
                or (msg.get("subtype") in ("bot_message",) and msg.get("bot_id") == bot_id)
            )
            if not is_bot_msg:
                continue
            try:
                client.chat_delete(channel=channel_id, ts=msg["ts"])
                deleted += 1
                time.sleep(0.3)  # rate limit
            except Exception as e:
                logger.warning(f"cleanup: nie udało się usunąć {msg['ts']}: {e}")
                errors += 1

        if resp.get("has_more") and resp.get("response_metadata", {}).get("next_cursor"):
            cursor = resp["response_metadata"]["next_cursor"]
        else:
            break

    status = f"✅ Usunięto *{deleted}* wiadomości bota"
    if errors:
        status += f" _(błędy przy {errors} wiadomościach)_"
    respond(status)


# ── /onboard slash command ────────────────────────────────────────────────────

app.command("/onboard")(handle_onboard_slash)
logger.info("✅ /onboard handler zarejestrowany")


# ── message events (DM + channel triggers) ───────────────────────────────────

@app.event("message")
def handle_message_events(body, say, logger):
    logger.info(body)
    event = body["event"]

    # Helper: odpowiada w tym samym wątku co wiadomość usera.
    # thread_ts = wątek istniejący LUB ts bieżącej wiadomości (tworzy nowy wątek).
    # Dzięki temu odpowiedź bota jest zawsze w tej samej konwersacji (Chat),
    # a nie jako osobny wpis w History.
    _dm_thread_ts = event.get("thread_ts") or event.get("ts")
    def _say_dm(text="", **_kw):
        _txt = text or _kw.get("text", "")
        app.client.chat_postMessage(
            channel=event.get("channel"),
            text=_txt,
            thread_ts=_dm_thread_ts,
        )

    if event.get("channel_type") == "im" and event.get("user") in _ctx.checkin_responses:
        user_id_ci  = event["user"]
        user_msg_ci = (event.get("text") or "").strip()
        entry       = _ctx.checkin_responses[user_id_ci]

        if entry.get("done"):
            return

        finish_kw = ["gotowe", "done", "koniec", "to wszystko",
                     "skończyłem", "skończyłam", "to tyle", "gotowy", "gotowa", "finish"]
        if any(kw in user_msg_ci.lower() for kw in finish_kw):
            if entry["messages"]:
                entry["done"] = True
                _say_dm("✅ *Dzięki za check-in!* Zapisałem Twój feedback na ten tydzień. Miłego weekendu! 🙏")
            else:
                _say_dm("🤔 Nie mam jeszcze żadnych Twoich odpowiedzi. Napisz coś zanim napiszesz *gotowe*!")
            return

        entry["messages"].append(user_msg_ci)
        if len(entry["messages"]) == 1:
            _say_dm("✍️ Zapisuję. Odpowiedz na pozostałe pytania i napisz *gotowe* kiedy skończysz.")
        return

    if event.get("bot_id"):
        return
    if event.get("subtype") in ("bot_message", "message_changed", "message_replied",
                                 "message_deleted", "thread_broadcast"):
        return

    user_message = event.get("text", "")
    user_id      = event.get("user")

    # === GŁOSÓWKI: transkrybuj pliki audio z Whisper ===
    _event_files = event.get("files") or []
    _audio_files = [f for f in _event_files if f.get("mimetype", "") in SLACK_AUDIO_MIMES
                    or f.get("subtype") == "slack_audio"]
    if _audio_files:
        _transcripts = []
        for _af in _audio_files:
            _tr = transcribe_slack_audio(_af["id"])
            if _tr:
                _transcripts.append(_tr)
        if _transcripts:
            _tr_text = " ".join(_transcripts)
            user_message = (user_message + " " + _tr_text).strip() if user_message else _tr_text

    # Guard: jeśli głosówka bez transkrypcji — poinformuj i zakończ
    if not user_message.strip() and _audio_files:
        _say_dm("🎤 Otrzymałem głosówkę, ale nie udało mi się jej przetranksrybować. Napisz co chciałeś przekazać — odpiszę od razu!")
        return

    text_lower = user_message.lower()

    # === ONBOARDING: "done N" w wątku onboardingowym ===
    if _handle_onboarding_done(event, say):
        return

    # === KANAŁY (pub/priv): reaguj tylko na "seba" lub "sebol" bez @wzmianki ===
    _ch_type = event.get("channel_type") or ""
    _ch_id   = event.get("channel", "")
    if not _ch_type:
        if _ch_id.startswith("C"):
            _ch_type = "channel"
        elif _ch_id.startswith("G"):
            _ch_type = "group"
    logger.info(f"MSG EVENT → channel_type={_ch_type!r} ch={_ch_id} text={user_message[:60]!r}")

    # === /kampania WIZARD: obsłuż odpowiedzi z wątku na kanale ===
    if user_id in _ctx.campaign_wizard:
        _wiz = _ctx.campaign_wizard[user_id]
        _wiz_ch  = _wiz.get("source_channel")
        _wiz_tts = _wiz.get("thread_ts")
        _msg_tts = event.get("thread_ts")
        if _ch_id == _wiz_ch and _msg_tts == _wiz_tts:
            def _wiz_say(text):
                _wizard_post(user_id, text)
            if _handle_campaign_wizard(user_id, user_message, event.get("files") or [], _wiz_say):
                return

    # === STANDUP: przechwytuj odpowiedzi z DM ===
    if _ch_type == "im":
        try:
            _st_info = app.client.users_info(user=user_id)
            _st_name = (_st_info["user"].get("real_name")
                        or _st_info["user"].get("profile", {}).get("display_name")
                        or user_id)
        except Exception:
            _st_name = user_id
        if handle_standup_reply(user_id, _st_name, user_message, msg_channel=_ch_id):
            app.client.chat_postMessage(
                channel=_ch_id,
                text="✅ Dzięki! Zapisałem Twoją odpowiedź na standup.",
            )
            return

    if _ch_type in ("channel", "group", "mpim"):

        if user_message.startswith("<@"):
            return
        _seba_m = re.search(r'\bsebol\w*\b|\bseba\b', user_message, re.IGNORECASE)
        _msg_thread_ts = event.get("thread_ts")
        _in_bot_thread = _msg_thread_ts and (event.get("channel"), _msg_thread_ts) in _ctx.bot_threads
        # Głosówka na kanale — traktuj jako trigger bez potrzeby mówienia "seba"
        if not _seba_m and not _audio_files and not _in_bot_thread:
            return
        logger.info(f"SEBA TRIGGER → {user_message!r} (thread={_in_bot_thread})")
        _clean = re.sub(r'\bsebol\w*\b|\bseba\b', "", user_message, count=1, flags=re.IGNORECASE).strip()
        handle_mention({**event, "text": f"<@SEBOL> {_clean}"}, say)
        return

    # Digest triggers — tylko w kanałach
    if any(t in text_lower for t in ["digest test", "test digest", "digest", "raport"]):
        if event.get("channel_type") != "im":
            channel_id  = event.get("channel")
            client_name = CHANNEL_CLIENT_MAP.get(channel_id)
            if client_name == "dre":
                say(generate_daily_digest_dre())
            else:
                say("Dla którego klienta? Dostępne: `dre` (wpisz np. `digest test dre`)")
            return

    # === ADS COMMANDS w DM i kanałach ===
    _ads_dm_match = re.search(
        r'\bads\s+(health|anomalies|anomalie|pacing|winners|losers)\b(.*)',
        text_lower
    )
    if _ads_dm_match:
        _dispatch_ads_command(
            _ads_dm_match.group(1).strip(),
            event.get("channel", ""),
            _ads_dm_match.group(2).strip(),
            say,
        )
        return

    # === DM handling ===
    if event.get("channel_type") == "im":
        try:
            user_info = app.client.users_info(user=user_id)
            user_name = (user_info["user"].get("real_name")
                         or user_info["user"].get("profile", {}).get("display_name")
                         or user_info["user"].get("name", user_id))
        except Exception:
            user_name = user_id

        # === NAPISZ DO: "napisz do X: treść" w DM do bota ===
        if re.search(r'\bnapisz\s+do\b', user_message, re.IGNORECASE):
            _dm_cmds = _parse_send_dm_commands(user_message)
            if _dm_cmds:
                _dm_results = []
                for _cmd in _dm_cmds:
                    _member = _resolve_team_member(_cmd["name"])
                    if not _member:
                        _dm_results.append(f"❌ Nie znam osoby *{_cmd['name']}*")
                        continue
                    if _cmd["time"]:
                        try:
                            _ts = _parse_schedule_time(_cmd["time"])
                            _dm_ch = app.client.conversations_open(users=_member["slack_id"])["channel"]["id"]
                            app.client.chat_scheduleMessage(
                                channel=_dm_ch,
                                text=_cmd["message"],
                                post_at=_ts,
                            )
                            _dm_results.append(f"✅ Zaplanowano do *{_member['name']}* o {_cmd['time']}: _{_cmd['message']}_")
                        except Exception as _e:
                            _dm_results.append(f"❌ Błąd planowania do {_member['name']}: {_e}")
                    else:
                        try:
                            _dm_ch = app.client.conversations_open(users=_member["slack_id"])["channel"]["id"]
                            app.client.chat_postMessage(
                                channel=_dm_ch,
                                text=_cmd["message"],
                            )
                            _dm_results.append(f"✅ Wysłano do *{_member['name']}*: _{_cmd['message']}_")
                        except Exception as _e:
                            _dm_results.append(f"❌ Błąd wysyłania do {_member['name']}: {_e}")
                if _dm_results:
                    _say_dm("\n".join(_dm_results))
                    return

        # === CAMPAIGN approve/cancel (DM) ===
        _dm_text_l    = user_message.lower()
        _dm_approve_m = re.search(r'(zatwierdź|zatwierdz|uruchom)\s+kampanię\s+(\d+)', _dm_text_l)
        _dm_cancel_m  = re.search(r'(anuluj|usuń|usun|skasuj)\s+kampanię\s+(\d+)', _dm_text_l)

        if _dm_approve_m:
            _camp_id = _dm_approve_m.group(2)
            _say_dm(text=f"🚀 Uruchamiam kampanię `{_camp_id}`...")
            _say_dm(text=approve_and_launch_campaign(_camp_id))
            return

        if _dm_cancel_m:
            _camp_id = _dm_cancel_m.group(2)
            _say_dm(text=cancel_campaign_draft(_camp_id))
            return

        # Guard: tylko jeśli message zawiera znane employee keywords
        if any(kw in _dm_text_l for kw in EMPLOYEE_MSG_KEYWORDS):
            if handle_employee_dm(user_id, user_name, user_message, _say_dm):
                return

    # Email summary trigger — wyniki zawsze na DM
    if any(t in text_lower for t in ["test email", "email test", "email summary"]):
        logger.info(f"📧 Email trigger od {user_id}, channel_type={event.get('channel_type')}")
        _say_dm("📧 Uruchamiam Email Summary... zaraz wrzucę tutaj.")
        try:
            email_config = get_user_email_config("UTE1RN6SJ")
            if not email_config:
                _say_dm("❌ Brak konfiguracji email (`EMAIL_ACCOUNTS`). Napisz do admina.")
                return
            daily_email_summary_slack()
        except Exception as e:
            _say_dm(f"❌ Błąd: `{str(e)}`")
            logger.error(f"Błąd test email trigger: {e}")
        return

    # Store incoming user message to long-term memory (before building history)
    remember(user_id, event.get("channel", ""), event.get("ts", ""), "user", user_message)

    # ── Build conversation history from memory DB (full history, like Claude.ai) ──
    # get_history returns last 500 messages chronologically — covers months of chat
    _history_msgs = get_history(user_id, limit=500)

    # Append current message if not already last in history
    if not _history_msgs or _history_msgs[-1]["content"] != user_message:
        _history_msgs.append({"role": "user", "content": user_message})

    # Merge consecutive same-role messages (Anthropic requires alternating roles)
    _merged: list[dict] = []
    for _m in _history_msgs:
        if _merged and _merged[-1]["role"] == _m["role"]:
            _merged[-1]["content"] += "\n" + _m["content"]
        else:
            _merged.append(dict(_m))
    while _merged and _merged[0]["role"] != "user":
        _merged.pop(0)
    if not _merged:
        _merged = [{"role": "user", "content": user_message}]

    _today_dm = datetime.now()
    _dm_system = (
        f"Dzisiaj: {_today_dm.strftime('%d %B %Y')} ({_today_dm.strftime('%Y-%m-%d')}).\n\n"
        "Jesteś Sebol — asystent agencji marketingowej Pato. Rozmawiasz z pracownikiem przez DM na Slacku.\n"
        "NIE jesteś Claude od Anthropic — jesteś Seblem, botem stworzonym dla agencji Pato.\n"
        "Pomagasz z kampaniami (Meta Ads / Google Ads), emailami, kalendarzem, teamem, raportami i codzienną pracą agencji.\n\n"
        "Klienci Meta: 'instax/fuji', 'zbiorcze', 'drzwi dre'. Google: 'dre', 'dre 2024', 'dre 2025', 'm2', 'pato'.\n"
        "Benchmarki Meta: ROAS >3.0, CTR 1.5-2.5%, CPC 3-8 PLN. Google Search: CTR 2-5%, CPC 2-10 PLN.\n\n"
        "⚠️ KONTEKST ROZMOWY: Czytaj historię wiadomości UWAŻNIE. Odpowiadaj na to co jest AKTUALNIE omawiane — "
        "jeśli rozmowa dotyczy kalendarza, odpowiadaj o kalendarzu; jeśli emaili — o emailach. "
        "NIE przekierowuj na kampanie gdy user pyta o coś innego!\n\n"
        "Mów po polsku. Bądź bezpośredni i konkretny — podawaj liczby, nie ogólniki. "
        "Emoji: 📊 💰 🚀 ⚠️ ✅"
    )
    _dm_tools = [
        {
            "name": "get_meta_ads_data",
            "description": "Pobiera statystyki z Meta Ads (Facebook/Instagram). Użyj gdy pytają o kampanie, spend, ROAS, CTR, konwersje.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "client_name":   {"type": "string"},
                    "date_from":     {"type": "string"},
                    "date_to":       {"type": "string"},
                    "level":         {"type": "string", "enum": ["campaign", "adset", "ad"]},
                    "campaign_name": {"type": "string"},
                    "metrics":       {"type": "array", "items": {"type": "string"}},
                    "breakdown":     {"type": "string"},
                    "limit":         {"type": "integer"},
                },
                "required": [],
            },
        },
        {
            "name": "get_google_ads_data",
            "description": "Pobiera statystyki z Google Ads. Użyj gdy pytają o kampanie Google, search, display.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "client_name":   {"type": "string"},
                    "date_from":     {"type": "string"},
                    "date_to":       {"type": "string"},
                    "level":         {"type": "string", "enum": ["campaign", "adgroup", "ad"]},
                    "campaign_name": {"type": "string"},
                    "metrics":       {"type": "array", "items": {"type": "string"}},
                    "limit":         {"type": "integer"},
                },
                "required": [],
            },
        },
        {
            "name": "manage_email",
            "description": "Zarządza emailami — czyta, wysyła, przeszukuje skrzynkę.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action":  {"type": "string", "enum": ["read", "send", "search"]},
                    "limit":   {"type": "integer"},
                    "to":      {"type": "string"},
                    "subject": {"type": "string"},
                    "body":    {"type": "string"},
                    "query":   {"type": "string"},
                },
                "required": ["action"],
            },
        },
        {
            "name": "get_ga4_data",
            "description": "Pobiera dane z Google Analytics 4: sesje, użytkownicy, konwersje, źródła ruchu, bounce rate. Użyj gdy pytają o ruch na stronie, GA, analytics.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "client_name": {"type": "string"},
                    "date_from":   {"type": "string"},
                    "date_to":     {"type": "string"},
                    "dimensions":  {"type": "array", "items": {"type": "string"}},
                    "metrics":     {"type": "array", "items": {"type": "string"}},
                    "limit":       {"type": "integer"},
                },
                "required": [],
            },
        },
        {
            "name": "manage_calendar",
            "description": "Zarządza kalendarzem iCloud — lista wydarzeń lub tworzenie nowego. Użyj gdy pytają o swój plan dnia/tygodnia, spotkania lub chcą dodać wydarzenie.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action":        {"type": "string", "enum": ["list", "create"]},
                    "date_from":     {"type": "string"},
                    "date_to":       {"type": "string"},
                    "title":         {"type": "string"},
                    "start":         {"type": "string"},
                    "end":           {"type": "string"},
                    "location":      {"type": "string"},
                    "description":   {"type": "string"},
                    "calendar_name": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    ]

    try:
        _resp = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=_dm_system,
            tools=_dm_tools,
            messages=_merged,
        )
        while _resp.stop_reason == "tool_use":
            _tb = next(b for b in _resp.content if b.type == "tool_use")
            if _tb.name == "get_meta_ads_data":
                _tr = meta_ads_tool(**{k: v for k, v in _tb.input.items() if v is not None})
            elif _tb.name == "get_google_ads_data":
                _tr = google_ads_tool(**{k: v for k, v in _tb.input.items() if v is not None})
            elif _tb.name == "get_ga4_data":
                _tr = google_analytics_tool(**{k: v for k, v in _tb.input.items() if v is not None})
            elif _tb.name == "manage_email":
                _inp = {k: v for k, v in _tb.input.items() if v is not None and k != "user_id"}
                _tr  = email_tool(user_id=user_id, **_inp)
            elif _tb.name == "manage_calendar":
                _owner_id = os.environ.get("CALENDAR_OWNER_SLACK_ID")
                if _owner_id and user_id != _owner_id:
                    _tr = {"error": "Brak dostępu — kalendarz jest prywatny."}
                else:
                    _tr = icloud_calendar_tool(**{k: v for k, v in _tb.input.items() if v is not None})
            else:
                _tr = {"error": "Nieznane narzędzie"}
            _merged.append({"role": "assistant", "content": _resp.content})
            _merged.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": _tb.id, "content": str(_tr)}]})
            _resp = anthropic.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                system=_dm_system,
                tools=_dm_tools,
                messages=_merged,
            )
        response_text = next(
            (b.text for b in _resp.content if hasattr(b, "text")),
            "Przepraszam, nie mogłem wygenerować odpowiedzi.",
        )
        _say_dm(text=response_text)
        # Store bot reply to long-term memory
        remember(user_id, event.get("channel", ""), event.get("ts", "") + "_bot", "assistant", response_text)
    except Exception as e:
        logger.error(f"Błąd DM handler: {e}")
        _say_dm(text=f"Przepraszam, wystąpił błąd: {str(e)}")


# ── /standup slash command ────────────────────────────────────────────────────

app.command("/standup")(handle_standup_slash)
logger.info("✅ /standup handler zarejestrowany")


# ── /kampania slash command ───────────────────────────────────────────────────

WIZARD_STEPS = [
    {
        "key": "klient",
        "q": (
            "🏢 *Krok 1/9 — Klient*\n"
            "Dla kogo kampania?\n"
            "`dre` / `instax` / `m2` / `pato`"
        ),
    },
    {
        "key": "cel",
        "q": (
            "🎯 *Krok 2/9 — Cel kampanii*\n"
            "`traffic` / `sprzedaż` / `leady` / `rozpoznawalność`"
        ),
    },
    {
        "key": "budzet",
        "q": (
            "💰 *Krok 3/9 — Budżet dzienny*\n"
            "Ile PLN/dzień? (np. `50` lub `200`)"
        ),
    },
    {
        "key": "url",
        "q": (
            "🔗 *Krok 4/9 — URL docelowy*\n"
            "Adres strony (np. `dre.eu`) lub napisz `bez linku`"
        ),
    },
    {
        "key": "czas",
        "q": (
            "📅 *Krok 5/9 — Czas trwania*\n"
            "np. `7 dni` / `14 dni` / `12-20 marca`"
        ),
    },
    {
        "key": "target",
        "q": (
            "👥 *Krok 6/9 — Grupa docelowa*\n"
            "Wiek, płeć, zainteresowania\n"
            "np. `kobiety 25-40, zainteresowania: wnętrza, dom`"
        ),
    },
    {
        "key": "tekst",
        "q": (
            "✍️ *Krok 7/9 — Tekst reklamy*\n"
            "Headline i copy\n"
            "np. `Nowe drzwi DRE — styl i jakość. Sprawdź ofertę!`"
        ),
    },
    {
        "key": "kreacje",
        "q": (
            "🖼️ *Krok 8/9 — Kreacje*\n"
            "Wyślij pliki graficzne/wideo LUB napisz:\n"
            "`pobierz z netu` — pobiorę ze strony klienta\n"
            "`bez kreacji` — tylko tekst"
        ),
    },
    {
        "key": "placements",
        "q": (
            "📱 *Krok 9/9 — Placements*\n"
            "`automatic` / `facebook` / `instagram` / `oba`"
        ),
    },
]


def _wizard_post(user_id: str, text: str):
    """Post wizard message to source_channel in wizard thread."""
    wizard = _ctx.campaign_wizard.get(user_id)
    if not wizard:
        return
    ch = wizard.get("source_channel")
    ts = wizard.get("thread_ts")
    try:
        app.client.chat_postMessage(channel=ch, thread_ts=ts, text=text)
    except Exception as e:
        logger.error("_wizard_post error: %s", e)


def _wizard_summary(answers: dict) -> str:
    labels = {
        "klient": "Klient", "cel": "Cel", "budzet": "Budżet dzienny",
        "url": "URL", "czas": "Czas", "target": "Grupa docelowa",
        "tekst": "Tekst", "kreacje": "Kreacje", "placements": "Placements",
    }
    lines = [f"• *{labels.get(k, k)}:* {v}" for k, v in answers.items()]
    return "\n".join(lines)


@app.command("/kampania")
def handle_kampania_slash(ack, command, logger):
    ack()
    user_id = command["user_id"]
    source_channel = command.get("channel_id", "")

    intro = (
        "🚀 *Tworzymy nową kampanię Meta Ads!*\n"
        "Odpowiedz na 9 pytań — buduję kampanię od razu po ostatnim.\n"
        "Napisz `anuluj` żeby przerwać w dowolnym momencie.\n\n"
        + WIZARD_STEPS[0]["q"]
    )
    try:
        resp = app.client.chat_postMessage(channel=source_channel, text=intro)
        thread_ts = resp["ts"]
    except Exception as e:
        logger.error("/kampania post error: %s", e)
        return

    _ctx.campaign_wizard[user_id] = {
        "step": 0,
        "answers": {},
        "files": [],
        "source_channel": source_channel,
        "thread_ts": thread_ts,
    }
    logger.info("/kampania started by %s in %s thread %s", user_id, source_channel, thread_ts)


logger.info("✅ /kampania handler zarejestrowany")


def _handle_campaign_wizard(user_id: str, user_message: str, files: list, say_fn) -> bool:
    """
    Handle a channel thread reply for a user in the /kampania wizard.
    Returns True if message was consumed by the wizard, False otherwise.
    """
    if user_id not in _ctx.campaign_wizard:
        return False

    wizard = _ctx.campaign_wizard[user_id]
    step_idx = wizard["step"]

    # Anulowanie
    if user_message.strip().lower() in ("anuluj", "cancel", "stop", "przerwij"):
        del _ctx.campaign_wizard[user_id]
        say_fn("❌ Tworzenie kampanii anulowane.")
        return True

    current_step = WIZARD_STEPS[step_idx]
    key = current_step["key"]

    # Kreacje — obsłuż pliki
    if key == "kreacje" and files:
        downloaded = download_slack_files([f["id"] for f in files])
        wizard["files"].extend(downloaded)
        wizard["answers"][key] = f"{len(downloaded)} plik(i) załączone"
    else:
        wizard["answers"][key] = user_message.strip()

    next_step = step_idx + 1

    # Wszystkie kroki wypełnione → podsumowanie i budowanie
    if next_step >= len(WIZARD_STEPS):
        del _ctx.campaign_wizard[user_id]
        summary = _wizard_summary(wizard["answers"])
        say_fn(
            f"✅ *Mam wszystko! Oto podsumowanie:*\n\n{summary}\n\n"
            "⏳ Buduję kampanię..."
        )
        # Build campaign params from wizard answers
        _build_campaign_from_wizard(user_id, wizard, say_fn)
        return True

    # Następne pytanie
    wizard["step"] = next_step
    say_fn(WIZARD_STEPS[next_step]["q"])
    return True


def _build_campaign_from_wizard(user_id: str, wizard: dict, say_fn):
    """Convert wizard answers to campaign params and create the campaign."""
    a = wizard["answers"]

    # Map answers → campaign params
    _obj_map = {
        "traffic": "OUTCOME_TRAFFIC",
        "sprzedaż": "OUTCOME_SALES",
        "sprzedaz": "OUTCOME_SALES",
        "leady": "OUTCOME_LEADS",
        "rozpoznawalność": "OUTCOME_AWARENESS",
        "rozpoznawalnosc": "OUTCOME_AWARENESS",
        "awareness": "OUTCOME_AWARENESS",
    }
    _placement_map = {
        "facebook": ["facebook_newsfeed"],
        "instagram": ["instagram_stream"],
        "oba": ["facebook_newsfeed", "instagram_stream"],
        "automatic": [],
    }

    # Parse budget
    _budzet_raw = re.sub(r"[^\d.,]", "", a.get("budzet", "50")).replace(",", ".")
    try:
        daily_budget = float(_budzet_raw)
    except ValueError:
        daily_budget = 50.0

    # Parse duration / dates
    _czas = a.get("czas", "7 dni").lower()
    _start = datetime.now().strftime("%Y-%m-%d")
    _days_match = re.search(r"(\d+)\s*dni", _czas)
    if _days_match:
        _end = (datetime.now() + timedelta(days=int(_days_match.group(1)))).strftime("%Y-%m-%d")
    else:
        _date_match = re.findall(r"(\d{1,2})[.\-\s]+(\d{1,2})(?:[.\-\s]+(\d{4}))?", _czas)
        if len(_date_match) >= 2:
            _m = datetime.now().month
            _y = datetime.now().year
            try:
                _start = datetime(_y, int(_date_match[0][1]) or _m, int(_date_match[0][0])).strftime("%Y-%m-%d")
                _end   = datetime(_y, int(_date_match[1][1]) or _m, int(_date_match[1][0])).strftime("%Y-%m-%d")
            except Exception:
                _end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        else:
            _end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    params = {
        "client_name":    a.get("klient", "dre"),
        "objective":      _obj_map.get(a.get("cel", "traffic").lower(), "OUTCOME_TRAFFIC"),
        "daily_budget":   daily_budget,
        "destination_url": a.get("url", ""),
        "start_date":     _start,
        "end_date":       _end,
        "targeting":      {"text": a.get("target", "")},
        "ad_text":        a.get("tekst", ""),
        "campaign_name":  f"Kampania {a.get('klient','').upper()} — {datetime.now().strftime('%d.%m.%Y')}",
        "placements":     _placement_map.get(a.get("placements", "automatic").lower(), []),
        "call_to_action": "LEARN_MORE",
    }

    try:
        account_id = get_meta_account_id(params["client_name"])
        creatives  = wizard["files"]

        if a.get("kreacje", "").lower() == "pobierz z netu" and params.get("destination_url"):
            say_fn("🌐 Pobieram kreacje ze strony klienta...")
            # Use parse_campaign_request to handle web scraping via existing flow
            _extra = parse_campaign_request(
                f"pobierz kreacje z {params['destination_url']}", []
            )
            if _extra.get("scraped_creatives"):
                creatives = _extra["scraped_creatives"]

        targeting = build_meta_targeting(params.get("targeting") or {})

        if creatives:
            say_fn(f"🎨 Uploaduję {len(creatives)} kreacji do Meta...")
            uploaded = []
            for name, data, mime in creatives:
                try:
                    cr = upload_creative_to_meta(account_id, data, mime, name)
                    uploaded.append(cr)
                except Exception as e:
                    say_fn(f"⚠️ Nie udało się uploadować `{name}`: {e}")
            creatives = uploaded

        say_fn("📋 Tworzę szkic kampanii w Meta Ads...")
        draft_ids = create_campaign_draft(account_id, params, creatives, targeting)
        preview   = generate_campaign_preview(params, params.get("targeting") or {}, len(creatives), draft_ids)
        say_fn(preview)

    except Exception as e:
        logger.error("_build_campaign_from_wizard error: %s", e)
        say_fn(f"❌ Błąd tworzenia kampanii: {e}")


# ── scheduler ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler(timezone=pytz.timezone('Europe/Warsaw'))
scheduler.add_job(daily_summaries,           'cron', day_of_week='mon-fri', hour=16, minute=0)
scheduler.add_job(daily_digest_dre,          'cron', day_of_week='mon-fri', hour=9, minute=0, id='daily_digest_dre')
scheduler.add_job(weekly_checkin,            'cron', day_of_week='fri', hour=14, minute=0)
scheduler.add_job(send_checkin_reminders,    'cron', day_of_week='fri', hour=17, minute=30, id='checkin_reminders')
scheduler.add_job(checkin_summary,           'cron', day_of_week='mon', hour=9,  minute=0)
scheduler.add_job(check_budget_alerts,       'cron', minute=0, id='budget_alerts')
scheduler.add_job(weekly_report_dre,         'cron', day_of_week='fri', hour=16, minute=0, id='weekly_reports')
scheduler.add_job(weekly_learnings_dre,      'cron', day_of_week='mon,thu', hour=8, minute=30, id='weekly_learnings')
scheduler.add_job(daily_email_summary_slack, 'cron', hour=16, minute=0, id='daily_email_summary')
scheduler.add_job(send_daily_team_availability, 'cron', day_of_week='mon-fri', hour=17, minute=0, id='team_availability')
scheduler.add_job(check_stale_onboardings,   'cron', hour=9, minute=30, id='stale_onboardings')
scheduler.add_job(send_standup_questions,    'cron', day_of_week='mon-fri', hour=9, minute=0,  id='standup_send')
scheduler.add_job(post_standup_summary,      'cron', day_of_week='mon-fri', hour=9, minute=30, id='standup_summary')
scheduler.add_job(weekly_industry_news,      'cron', day_of_week='mon',     hour=9, minute=0,  id='industry_news')
scheduler.start()

print(f"✅ Scheduler załadowany! Jobs: {len(scheduler.get_jobs())}")
print("✅ Scheduler wystartował!")

# Odbuduj dane nieobecności z historii Slacka po starcie/deployu
try:
    sync_availability_from_slack()
except Exception as _e:
    print(f"⚠️ sync_availability_from_slack startup error: {_e}")

# ── memory backfill (runs once in background on startup) ─────────────────────
import threading, sqlite3 as _sqlite3
def _run_backfill_if_empty():
    try:
        from tools.memory import DB_PATH
        with _sqlite3.connect(DB_PATH) as _c:
            _count = _c.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
        if _count == 0:
            logger.info("Memory DB empty — running backfill from Slack history...")
            from tools.memory_backfill import run_backfill
            run_backfill(days=365)
        else:
            logger.info("Memory DB has %d messages — skipping backfill", _count)
    except Exception as _e:
        logger.warning("Memory backfill error: %s", _e)
threading.Thread(target=_run_backfill_if_empty, daemon=True).start()

# ── start ─────────────────────────────────────────────────────────────────────

handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
print("⚡️ Bot działa!")
handler.start()
