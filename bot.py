import os
import re
import json
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
    CAMPAIGN_CHANNEL_ID,
)

# ── tools ─────────────────────────────────────────────────────────────────────
from tools.meta_ads import meta_ads_tool
from tools.google_ads import (
    google_ads_tool,
    create_google_campaign_draft,
    generate_google_campaign_preview,
    _detect_google_client,
)
from tools.google_analytics import google_analytics_tool
from tools.email_tools import email_tool, get_user_email_config
from tools.slack_tools import slack_read_channel_tool, slack_read_thread_tool

# ── jobs ──────────────────────────────────────────────────────────────────────
from jobs.performance_analysis import _dispatch_ads_command, backfill_campaign_history
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
# jobs.reminders removed — reminders now use Slack chat.scheduleMessage
from tools.campaign_creator import (
    download_slack_files, upload_creative_to_meta, parse_campaign_request,
    build_meta_targeting, create_campaign_draft, generate_campaign_preview,
    approve_and_launch_campaign, cancel_campaign_draft, validate_campaign_params,
    get_meta_account_id, generate_campaign_expert_analysis,
)
from tools.voice_transcription import transcribe_slack_audio, SLACK_AUDIO_MIMES
from tools.icloud_calendar import icloud_calendar_tool
from tools.google_slides import create_presentation
from tools.memory import init_memory, remember, recall_as_context, get_history
from tools.reminders import init_reminders, schedule_reminder, list_reminders

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _SlackErrorHandler(logging.Handler):
    """Forwards ERROR+ log records to a Slack channel in a background thread.
    Uses a 30-second per-message cooldown to avoid alert spam.
    Registered after `app` is initialized (see bottom of file).
    """
    def __init__(self, channel_id: str):
        super().__init__(level=logging.ERROR)
        self._channel = channel_id
        self._cooldown: dict = {}  # msg_key → last_sent_timestamp

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            key = msg[:120]
            now = time.time()
            if now - self._cooldown.get(key, 0) < 30:
                return
            self._cooldown[key] = now
            import threading as _t
            _t.Thread(
                target=app.client.chat_postMessage,
                kwargs={
                    "channel": self._channel,
                    "text": f"❌ *{record.levelname}* `{record.name}`\n```{msg[:2000]}```",
                },
                daemon=True,
            ).start()
        except Exception:
            pass  # never let a logging handler crash the bot


# ── initialization ────────────────────────────────────────────────────────────
_ctx.app    = App(token=os.environ.get("SLACK_BOT_TOKEN"))
from tools.token_log import LoggingAnthropicWrapper, get_cost_summary as _token_cost_summary
_ctx.claude = LoggingAnthropicWrapper(Anthropic(api_key=os.environ.get("CLAUDE_API_KEY")))
init_memory()
init_reminders()
_ctx.load_wizard_state()  # Restore wizard sessions that survived restart

# Odtwórz nieobecności z historii Slacka po restarcie (Render usuwa pliki)
try:
    sync_availability_from_slack()
except Exception as _sync_err:
    logger.warning("startup sync_availability_from_slack failed: %s", _sync_err)

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
- create_presentation() → "zrób prezentację", "zrób prezke", "przygotuj ofertę dla klienta", "deck", "pitch deck", "raport w prezentacji"
  ⚠️ PRZED wywołaniem create_presentation ZAWSZE zbierz pełny kontekst — jeśli czegoś brakuje, zapytaj:
  1. Dla kogo jest prezentacja i jaki jest cel? (oferta sprzedażowa, raport wyników, onboarding, pitch?)
  2. Co ma zawierać? (jakie slajdy, tematy, dane?)
  3. Czy jest brief, dane, liczby, argumenty do uwzględnienia?
  4. Jaki ton/styl? (formalny dla klienta, wewnętrzny dla teamu?)
  Dopiero gdy masz odpowiedzi — sam napisz pełną treść każdego slajdu i wywołaj create_presentation z extra_slides wypełnionymi gotowym contentem.
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
        {
            "name": "create_presentation",
            "description": (
                "Tworzy prezentację w Google Slides i zwraca link. "
                "Użyj gdy ktoś prosi o prezentację, prezke, ofertę dla klienta, raport wyników reklam, "
                "deck dla klienta, pitch deck. Może zawierać dane z Google Ads, Meta Ads, "
                "brief klienta lub dowolne własne slajdy."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Tytuł prezentacji, np. 'Oferta dla OLX' lub 'Wyniki kampanii DRE – marzec 2026'."
                    },
                    "client_name": {
                        "type": "string",
                        "description": "Nazwa klienta (opcjonalne)."
                    },
                    "subtitle": {
                        "type": "string",
                        "description": "Podtytuł lub tagline na slajdzie tytułowym (opcjonalne)."
                    },
                    "brief": {
                        "type": "string",
                        "description": "Treść briefu, opis oferty lub kontekst — zostanie umieszczony na osobnym slajdzie (opcjonalne)."
                    },
                    "date_range": {
                        "type": "string",
                        "description": "Zakres dat, np. '01.03.2026 – 31.03.2026' (opcjonalne)."
                    },
                    "google_ads_data": {
                        "type": "object",
                        "description": "Dane z Google Ads (wynik get_google_ads_data) — opcjonalne."
                    },
                    "meta_ads_data": {
                        "type": "object",
                        "description": "Dane z Meta Ads (wynik get_meta_ads_data) — opcjonalne."
                    },
                    "extra_slides": {
                        "type": "array",
                        "description": "Lista dodatkowych slajdów z wolną treścią.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title":   {"type": "string", "description": "Nagłówek slajdu."},
                                "content": {"type": "string", "description": "Treść slajdu (bullet points, tekst)."}
                            },
                            "required": ["title", "content"]
                        }
                    }
                },
                "required": ["title"]
            }
        },
    ]

    try:
        user_id = event.get('user')

        # Store incoming message to long-term memory
        remember(user_id, channel, event.get("ts", ""), "user", user_message)

        contextual_message = (
            (channel_history_ctx + user_message) if channel_history_ctx else user_message
        )

        # For threaded messages: use thread context instead of global conversation history
        # This prevents old campaign data from bleeding into new thread conversations
        _event_thread_ts = event.get("thread_ts")
        if _event_thread_ts:
            try:
                _thread_result = app.client.conversations_replies(
                    channel=channel, ts=_event_thread_ts, limit=20
                )
                _thread_msgs = _thread_result.get("messages", [])
                _bot_uid = None
                try:
                    _bot_uid = app.client.auth_test()["user_id"]
                except Exception:
                    pass
                history = []
                for _tmsg in _thread_msgs:
                    _t_text = _tmsg.get("text", "")
                    if not _t_text:
                        _t_ts = _tmsg.get("ts", "")
                        _t_text = _ctx.voice_cache.get((channel, _t_ts), "")
                        if not _t_text:
                            continue
                    if _tmsg.get("user") == _bot_uid or _tmsg.get("bot_id"):
                        history.append({"role": "assistant", "content": _t_text})
                    else:
                        history.append({"role": "user", "content": _t_text})
                # Remove last user msg if it duplicates current message (will be added below)
                if history and history[-1]["role"] == "user":
                    history.pop()
            except Exception as e:
                logger.warning("Failed to fetch thread history: %s", e)
                history = get_conversation_history(user_id)
        else:
            history = get_conversation_history(user_id)

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
                elif tool_name == "create_presentation":
                    tool_result = create_presentation(
                        title=tool_input.get("title"),
                        client_name=tool_input.get("client_name"),
                        subtitle=tool_input.get("subtitle"),
                        brief=tool_input.get("brief"),
                        date_range=tool_input.get("date_range"),
                        google_ads_data=tool_input.get("google_ads_data"),
                        meta_ads_data=tool_input.get("meta_ads_data"),
                        extra_slides=tool_input.get("extra_slides"),
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
            # Cache transcription for thread history recovery
            _msg_ts = event.get("ts", "")
            _msg_ch = event.get("channel", "")
            if _msg_ts and _msg_ch:
                _ctx.voice_cache[(_msg_ch, _msg_ts)] = user_message

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
    logger.info(f"MSG EVENT → channel_type={_ch_type!r} ch={_ch_id} text={user_message[:60]!r}"
                f" thread_ts={event.get('thread_ts')!r}"
                f" wizards=[meta={user_id in _ctx.meta_campaign_wizard}"
                f" google={user_id in _ctx.google_campaign_wizard}"
                f" kampania={user_id in _ctx.campaign_wizard}]")

    # Pliki kreacji (bez audio — głosówki transkrybowane osobno wyżej)
    _creative_files = [f for f in _event_files
                       if f.get("mimetype", "") not in SLACK_AUDIO_MIMES
                       and f.get("subtype") != "slack_audio"]

    # === #tworzenie-kampanii: każdy wątek = izolowany kontekst kampanii (sprawdź PRZED wizardami) ===
    _ch_type_early = event.get("channel_type") or ""
    if not _ch_type_early:
        _ch_id_early = event.get("channel", "")
        if _ch_id_early.startswith("C"):
            _ch_type_early = "channel"
        elif _ch_id_early.startswith("G"):
            _ch_type_early = "group"
    _ch_id_early = event.get("channel", "")
    _msg_thread_ts_early = event.get("thread_ts")
    _is_campaign_ch_early = CAMPAIGN_CHANNEL_ID and _ch_id_early == CAMPAIGN_CHANNEL_ID
    _seba_m_early = re.search(r'\bsebol\w*\b|\bseba\b', user_message, re.IGNORECASE)
    # Sprawdź czy aktywny wizard obsługuje ten konkretny wątek — jeśli tak, nie przechwytuj
    _wizard_owns_thread = (
        (user_id in _ctx.meta_campaign_wizard
         and _ctx.meta_campaign_wizard[user_id].get("source_channel") == _ch_id_early
         and _ctx.meta_campaign_wizard[user_id].get("thread_ts") == _msg_thread_ts_early)
        or (user_id in _ctx.google_campaign_wizard
            and _ctx.google_campaign_wizard[user_id].get("source_channel") == _ch_id_early
            and _ctx.google_campaign_wizard[user_id].get("thread_ts") == _msg_thread_ts_early)
        or (user_id in _ctx.campaign_wizard
            and _ctx.campaign_wizard[user_id].get("source_channel") == _ch_id_early
            and _ctx.campaign_wizard[user_id].get("thread_ts") == _msg_thread_ts_early)
    )
    if (_ch_type_early in ("channel", "group", "mpim")
            and _is_campaign_ch_early
            and _msg_thread_ts_early
            and not _seba_m_early
            and not _wizard_owns_thread):
        _handle_campaign_channel_thread(event, user_message, say)
        return

    # === /kampania WIZARD: obsłuż odpowiedzi z wątku na kanale ===
    if user_id in _ctx.campaign_wizard:
        _wiz = _ctx.campaign_wizard[user_id]
        _wiz_ch  = _wiz.get("source_channel")
        _wiz_tts = _wiz.get("thread_ts")
        _msg_tts = event.get("thread_ts")
        if _ch_id == _wiz_ch and _msg_tts == _wiz_tts:
            def _wiz_say(text):
                _wizard_post(user_id, text)
            if _handle_campaign_wizard(user_id, user_message, _creative_files, _wiz_say):
                return

    # === /kampaniagoogle WIZARD: obsłuż odpowiedzi z wątku na kanale ===
    if user_id in _ctx.google_campaign_wizard:
        _gwiz = _ctx.google_campaign_wizard[user_id]
        _gwiz_ch  = _gwiz.get("source_channel")
        _gwiz_tts = _gwiz.get("thread_ts")
        _msg_tts  = event.get("thread_ts")
        logger.info(f"GOOGLE WIZARD CHECK → user={user_id} ch_match={_ch_id == _gwiz_ch} ts_match={_msg_tts == _gwiz_tts} (event_ts={_msg_tts} wiz_ts={_gwiz_tts})")
        if _ch_id == _gwiz_ch and _msg_tts == _gwiz_tts:
            def _gwiz_say(text):
                _google_wizard_post(user_id, text)
            if _handle_google_campaign_wizard(user_id, user_message, _creative_files, _gwiz_say):
                return

    # === /kampaniameta WIZARD: obsłuż odpowiedzi z wątku na kanale ===
    if user_id in _ctx.meta_campaign_wizard:
        _mwiz = _ctx.meta_campaign_wizard[user_id]
        _mwiz_ch  = _mwiz.get("source_channel")
        _mwiz_tts = _mwiz.get("thread_ts")
        _msg_tts  = event.get("thread_ts")
        logger.info(f"META WIZARD CHECK → user={user_id} ch_match={_ch_id == _mwiz_ch} ts_match={_msg_tts == _mwiz_tts} (event_ts={_msg_tts} wiz_ts={_mwiz_tts})")
        if _ch_id == _mwiz_ch and _msg_tts == _mwiz_tts:
            def _mwiz_say(text):
                _meta_wizard_post(user_id, text)
            if _handle_meta_campaign_wizard(user_id, user_message, _creative_files, _mwiz_say):
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
        # #tworzenie-kampanii: bot odpowiada na każdy wątek (bez potrzeby @mention)
        _is_campaign_ch = CAMPAIGN_CHANNEL_ID and _ch_id == CAMPAIGN_CHANNEL_ID
        _in_campaign_thread = _is_campaign_ch and _msg_thread_ts
        if _in_campaign_thread and not _seba_m:
            # Dedykowany handler — tylko kontekst kampanijny, bez memory/tools
            _handle_campaign_channel_thread(event, user_message, say)
            return
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
        "⚠️ KONTEKST ROZMOWY: Czytaj historię wiadomości UWAŻNIE. Odpowiadaj WYŁĄCZNIE na to o co user AKTUALNIE pyta. "
        "Jeśli pyta o reminder — tylko zapisz reminder i potwierdź. Jeśli o email — tylko email. "
        "ABSOLUTNY ZAKAZ: NIE startuj, NIE proponuj, NIE wspominaj tworzenia kampanii jeśli user NIE poprosił o kampanię w tej wiadomości. "
        "Jedna prośba = jedna odpowiedź. Nie doklejaj niczego niezwiązanego.\n\n"
        "Mów po polsku. Bądź bezpośredni i konkretny — podawaj liczby, nie ogólniki. "
        "Emoji: 📊 💰 ⚠️ ✅\n\n"
        "📌 REMINDERY: Gdy user prosi o przypomnienie ('przypomnij mi', 'zanotuj że', 'remind me') — "
        "ZAWSZE użyj narzędzia save_reminder żeby zapisać. NIE mów tylko 'zanotowałem' bez wywołania narzędzia. "
        "Po zapisaniu potwierdź datę i treść w MAX 2 zdaniach. Nic więcej."
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
        {
            "name": "save_reminder",
            "description": (
                "Zapisuje przypomnienie na konkretną datę — będzie automatycznie wysłane na Slacku o 9:00 tego dnia. "
                "Użyj gdy user prosi o przypomnienie, mówi 'przypomnij mi', 'zanotuj że X marca', 'wyślij mi reminder' itp. "
                "Podaj action='save' żeby zapisać, action='list' żeby pokazać zaplanowane remindery użytkownika."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action":      {"type": "string", "enum": ["save", "list"]},
                    "remind_date": {"type": "string", "description": "Data w formacie YYYY-MM-DD"},
                    "message":     {"type": "string", "description": "Treść przypomnienia — co dokładnie wysłać"},
                    "channel_id":  {"type": "string", "description": "Slack channel_id gdzie wysłać (domyślnie kanał rozmowy)"},
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
            elif _tb.name == "save_reminder":
                _action = _tb.input.get("action", "save")
                _r_chan = _tb.input.get("channel_id") or event.get("channel", "")
                if _action == "list":
                    _pending = list_reminders(app.client, _r_chan)
                    if _pending:
                        _tr = {"reminders": _pending, "count": len(_pending)}
                    else:
                        _tr = {"reminders": [], "message": "Brak zaplanowanych przypomnień."}
                else:
                    _r_date = _tb.input.get("remind_date")
                    _r_msg  = _tb.input.get("message", "")
                    if not _r_date or not _r_msg:
                        _tr = {"error": "Wymagane pola: remind_date (YYYY-MM-DD) i message."}
                    else:
                        try:
                            _rid = schedule_reminder(app.client, _r_chan, _r_date, _r_msg)
                            _tr = {"ok": True, "id": _rid, "remind_date": _r_date, "message": "Reminder zapisany — Slack wyśle o 9:00 tego dnia."}
                        except Exception as _re:
                            logger.error("schedule_reminder error: %s", _re)
                            _tr = {"error": str(_re)}
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


# ── /tokeny slash command ─────────────────────────────────────────────────────

@app.command("/tokeny")
def handle_tokeny_slash(ack, respond, command):
    """Pokazuje koszt tokenów Anthropic API z ostatnich N dni."""
    ack()
    text = (command.get("text") or "").strip()
    try:
        days = int(text) if text else 30
    except ValueError:
        days = 30
    respond(_token_cost_summary(days))


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


# ── #tworzenie-kampanii: dedykowany handler wątków ────────────────────────────

def _build_campaign_channel_system_prompt() -> str:
    from config.constants import META_ACCOUNT_IDS, META_PAGE_IDS
    account_lines = []
    seen = set()
    for key, acc_id in META_ACCOUNT_IDS.items():
        if acc_id and acc_id not in seen:
            seen.add(acc_id)
            account_lines.append(f"  - {key}: {acc_id}")
    page_lines = []
    seen_pages = set()
    for key, page_id in META_PAGE_IDS.items():
        if page_id and page_id not in seen_pages:
            seen_pages.add(page_id)
            page_lines.append(f"  - {key}: {page_id}")
    accounts_section = (
        "KONTA META ADS (znasz te dane — NIE pytaj użytkownika o konto):\n"
        + ("\n".join(account_lines) if account_lines else "  (brak skonfigurowanych kont)")
    )
    pages_section = (
        "STRONY META (Page IDs):\n"
        + ("\n".join(page_lines) if page_lines else "  (brak skonfigurowanych stron)")
    ) if page_lines else ""

    return (
        "Jesteś Sebol — asystent agencji marketingowej Pato, specjalista od tworzenia kampanii reklamowych.\n"
        "Rozmawiasz na kanale #tworzenie-kampanii.\n"
        "\n"
        "TWOJA ROLA: Pomagasz tworzyć nowe kampanie Meta Ads i Google Ads.\n"
        "Każda rozmowa w wątku = nowa kampania. NIE odwołuj się do żadnych istniejących kampanii.\n"
        "\n"
        + accounts_section + "\n"
        + (pages_section + "\n" if pages_section else "")
        + "\n"
        "Zachowanie:\n"
        "- Traktuj każdy wątek jako osobny brief na nową kampanię\n"
        "- Zbieraj dane potrzebne do utworzenia kampanii (cel, budżet, targetowanie, kreacje, link, placements)\n"
        "- Bądź konkretny, krótki, po polsku\n"
        "- Jeśli user podał dużo danych — potwierdź co masz i pytaj o brakujące\n"
        "- Jeśli user podał mało — zadaj 5-6 kluczowych pytań\n"
        "- NIE szukaj danych w pamięci, NIE odwołuj się do istniejących kampanii\n"
        "- NIE używaj narzędzi (Meta API, Google Ads itp.) — tylko zbieraj brief\n"
        "- Gdy masz klienta — podaj od razu które konto Meta Ads zostanie użyte (z listy powyżej)\n"
        "\n"
        "Gdy masz komplet danych, podsumuj kampanię i zapytaj czy uruchamiamy.\n"
    )


def _handle_campaign_channel_thread(event, user_message, say):
    """Handle messages in #tworzenie-kampanii threads — campaign-only context."""
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts", "")
    user_id = event.get("user", "")

    # Pobierz historię wątku z Slacka
    try:
        result = app.client.conversations_replies(
            channel=channel, ts=thread_ts, limit=50
        )
        thread_msgs = result.get("messages", [])
        # If paginated (next_cursor), fetch the rest to capture most recent messages
        while result.get("response_metadata", {}).get("next_cursor"):
            result = app.client.conversations_replies(
                channel=channel, ts=thread_ts, limit=50,
                cursor=result["response_metadata"]["next_cursor"]
            )
            thread_msgs.extend(result.get("messages", []))
    except Exception as e:
        logger.error("campaign channel thread fetch error: %s", e)
        thread_msgs = []

    # Zbuduj historię konwersacji z wątku
    bot_user_id = None
    try:
        bot_user_id = app.client.auth_test()["user_id"]
    except Exception:
        pass

    messages = []
    for msg in thread_msgs:
        msg_text = msg.get("text", "")
        # Recover voice message transcriptions from cache
        if not msg_text:
            msg_ts = msg.get("ts", "")
            cached = _ctx.voice_cache.get((channel, msg_ts), "")
            if cached:
                msg_text = cached
            else:
                continue
        if msg.get("user") == bot_user_id or msg.get("bot_id"):
            messages.append({"role": "assistant", "content": msg_text})
        else:
            messages.append({"role": "user", "content": msg_text})

    # Upewnij się że ostatnia wiadomość to user (np. głosówka z bieżącą transkrypcją)
    if not messages or messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": user_message})
    elif user_message and messages[-1]["content"] != user_message:
        # Bieżąca wiadomość (np. transkrypcja głosówki) nie jest w historii
        messages.append({"role": "user", "content": user_message})

    try:
        response = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=_build_campaign_channel_system_prompt(),
            messages=messages,
        )
        reply = response.content[0].text
        say(text=reply, thread_ts=thread_ts)
        _ctx.bot_threads.add((channel, thread_ts))
    except Exception as e:
        logger.error("campaign channel Claude error: %s", e)
        say(text=f"❌ Błąd: {e}", thread_ts=thread_ts)


# ── /kampaniameta — Claude-driven Meta Ads wizard (AUTO/SIMPLE/PRO) ────────────

META_CAMPAIGN_PRO_PROMPT = """\
⛔ ABSOLUTNY ZAKAZ — przeczytaj zanim cokolwiek napiszesz:
NIGDY nie pisz sekcji "CO DALEJ", "Gotowe!", "Kampania rozpocznie się", "Kampania jest aktywna",
"Spodziewaj się wyników", "Monitoring: Sprawdzę", ani niczego sugerującego że kampania istnieje lub ruszy —
jeśli NIE wygenerowałeś JSON z markerem ===KAMPANIA_META_GOTOWA===.
KLUCZOWE: Dopóki nie wygenerujesz JSON z ===KAMPANIA_META_GOTOWA=== — kampania NIE istnieje w Meta Ads.
Pisząc "Gotowe!" bez JSON okłamujesz użytkownika — to jest niedopuszczalne.
Po wygenerowaniu markera system tworzy kampanię AUTOMATYCZNIE i wyśle potwierdzenie. Ty kończysz na JSON.
⛔ KONIEC ZAKAZU

You are a Meta Ads campaign creation assistant in Slack (Sebol). PRO mode — full professional workflow.
Respond in Polish. Be concrete, structured, helpful.

CRITICAL RULE: NEVER invent, assume or fill in data the user did not explicitly provide.
If something is missing — ASK. Do NOT copy data from previous conversations. Each session starts fresh.

PRO Mode Workflow — follow these stages in order. Do NOT skip stages.

Stage 0 — Client Identification:
- CRITICAL: Always identify the client first. Extract from user message (e.g. "dre", "drzwi dre", "instax", "m2", "pato"). If the landing_page_url is "patoagencja.com" — that is the AGENCY's own website, NOT the client. If unclear — ASK. Fill "client_name" in the JSON.

Stage 1 — Business Basics:
- What product/service are we advertising?
- What is the main business goal?
- Who is the ideal customer?
- Which country/market?
- Campaign objective? (Leads / Sales / Traffic / Engagement / Messages / App installs / Video views)
- Daily or monthly budget?

Stage 2 — Funnel Structure:
- Cold audiences, warm audiences, remarketing, or full funnel?
- Separate campaigns for prospecting / retargeting / lookalikes?

Stage 3 — Offer and Landing Page:
- Landing page URL?
- Main offer? (discount, free consultation, ebook, demo, product purchase)
- What action should users take? (submit form, buy, book call, message)

Stage 4 — Audience Targeting:
- Locations, age range, gender, languages
- Audience strategy: interests, lookalikes, remarketing lists, customer lists
- Website visitors, video viewers, engaged users, past customers?

Stage 5 — Creative Assets:
- Videos, images, carousel, UGC?
- Primary text, headline, description, CTA
- Multiple creatives to test?

Stage 6 — Ad Set Structure:
- Multiple audiences? Creative testing? Budget split testing?
- Number of ad sets?

Stage 7 — Optimization:
- Optimize for which event? (Purchase, Lead, Add to cart, Landing page view, Messages)
- Is Meta Pixel installed?

Stage 8 — Budget and Schedule:
- Daily budget or lifetime budget?
- Start date, end date (optional)?

Ask questions in rounds (4-6 questions per round), not all at once.
If user gives incomplete answer — ask follow-up. Never guess critical data.

IMPORTANT — Completion signal:
When you have ALL required data and user confirms, output EXACTLY this marker:
===KAMPANIA_META_GOTOWA===
Then provide 4 sections:
1. **Campaign Summary** — objective, offer, audience, budget, location
2. **Campaign Structure** — Campaign → Ad Sets → Ads
3. **Risks / Missing Items** — what needs verification
4. **JSON**:
```json
{
  "mode": "pro", "client_name": "", "campaign_name": "", "objective": "", "business_goal": "",
  "budget_daily": "", "location": [], "audiences": [], "lookalike_audiences": [],
  "remarketing": [],
  "creative": {"images": [], "videos": [], "texts": [], "headlines": [], "cta": ""},
  "ad_sets": [], "optimization_event": "", "pixel_installed": false,
  "schedule": {"start_date": "", "end_date": ""},
  "missing_items": [], "ready_to_create": false
}
```
"""

META_CAMPAIGN_SIMPLE_PROMPT = """\
⛔ ABSOLUTNY ZAKAZ — przeczytaj zanim cokolwiek napiszesz:
NIGDY nie pisz sekcji "CO DALEJ", "Gotowe!", "Kampania rozpocznie się", "Kampania jest aktywna",
"Spodziewaj się wyników", "Monitoring: Sprawdzę", ani niczego sugerującego że kampania istnieje lub ruszy —
jeśli NIE wygenerowałeś JSON z markerem ===KAMPANIA_META_GOTOWA===.
KLUCZOWE: Dopóki nie wygenerujesz JSON z ===KAMPANIA_META_GOTOWA=== — kampania NIE istnieje w Meta Ads.
Pisząc "Gotowe!" bez JSON okłamujesz użytkownika — to jest niedopuszczalne.
Po wygenerowaniu markera system tworzy kampanię AUTOMATYCZNIE i wyśle potwierdzenie. Ty kończysz na JSON.
⛔ KONIEC ZAKAZU

You are a Meta Ads campaign creation assistant in Slack (Sebol). SIMPLE mode — fast campaign launch.
Respond in Polish. Be short, direct.

SIMPLE mode: collect only essential data, avoid long questionnaires, avoid strategic consulting.

CRITICAL RULE: NEVER invent, assume or fill in data the user did not explicitly provide.
If the user didn't mention targeting (age, gender, interests, location, placement) — ASK for it or use broad targeting.
Do NOT copy data from previous conversations. Each campaign wizard session starts fresh.

Required fields:
- client name (who is the campaign for: dre/instax/m2/pato — ALWAYS extract from user message or ask if not provided)
- campaign objective (Leads/Sales/Traffic/Engagement/Video views/Messages/App installs)
- daily budget
- country or location
- landing page URL
- creative assets (video/image + primary text + headline + CTA)
- basic audience (age, gender, interests — if user doesn't specify, use broad targeting and say so)

CRITICAL: Always fill "client_name" in the JSON. Extract from: campaign name, user's messages, context (e.g. "dre", "drzwi dre", "instax", "m2", "pato"). If the landing_page_url is "patoagencja.com" — that is the AGENCY's own website, NOT the client name. If client is unknown — ASK.

Ask all questions in ONE round (max 6-7 questions).
If user already provided data — do NOT ask again, just confirm and ask for missing items.
If user provides everything upfront — skip questions entirely, go straight to output.

Only probe if missing: client, budget, creative, URL, or objective.

SWITCHING TO PRO:
If conversation becomes complex (user wants strategy, multiple audiences, funnel design,
CPA/ROAS optimization, creative testing), START your response with:
===SWITCH:PRO===
Then explain you're switching to full setup mode.

IMPORTANT — Completion signal:
When enough data collected, output EXACTLY:
===KAMPANIA_META_GOTOWA===
Then provide 3 sections:
1. **Summary** — objective, budget, location, audience, creative type, landing page
2. **Campaign Structure** — Campaign → Ad Set → Ad (short)
3. **JSON**:
```json
{
  "mode": "simple", "client_name": "", "campaign_name": "", "objective": "", "daily_budget": "",
  "country": "", "age_range": "", "gender": "", "interests": [],
  "landing_page_url": "",
  "creative": {"type": "", "images": [], "videos": [], "primary_text": "", "headline": "", "cta": ""},
  "optimization_event": "", "tracking": {"pixel_installed": false},
  "missing_items": [], "ready_to_create": false
}
```
"""

META_CAMPAIGN_AUTO_PROMPT = """\
⛔ ABSOLUTNY ZAKAZ — przeczytaj zanim cokolwiek napiszesz:
NIGDY nie pisz sekcji "CO DALEJ", "Gotowe!", "Kampania rozpocznie się", "Kampania jest aktywna",
"Spodziewaj się wyników", "Monitoring: Sprawdzę", ani niczego sugerującego że kampania istnieje lub ruszy —
jeśli NIE wygenerowałeś JSON z markerem ===KAMPANIA_META_GOTOWA===.
KLUCZOWE: Dopóki nie wygenerujesz JSON z ===KAMPANIA_META_GOTOWA=== — kampania NIE istnieje w Meta Ads.
Pisząc "Gotowe!" bez JSON okłamujesz użytkownika — to jest niedopuszczalne.
Po wygenerowaniu markera system tworzy kampanię AUTOMATYCZNIE i wyśle potwierdzenie. Ty kończysz na JSON.
⛔ KONIEC ZAKAZU

You are a Meta Ads campaign creation assistant in Slack (Sebol).
Respond in Polish. Your job: analyze user's FIRST message and choose between SIMPLE and PRO mode.

CRITICAL RULE: NEVER invent, assume or fill in data the user did not explicitly provide.
Each wizard session starts completely fresh — no data from previous conversations.

Choose SIMPLE if:
- User wants speed: "szybko", "prosta kampania", "bez pytań", "minimum", "tylko odpal", "na szybko"
- User already provides: budget + link + creative + country
- Simple single-objective campaign (one video ad, one audience, one product)
- Signals: "wrzuć kampanię", "mam film i link", "zrób prostą kampanię", "just launch it"

Choose PRO if:
- User wants strategy, advice, or complex structure
- Multiple products/services, audiences, or funnels
- Signals: "strategia", "dobierz", "zoptymalizuj", "ROAS", "CPA", "remarketing",
  "segmentacja", "rozpisz", "dla klienta", "pełny setup", "co wybrać"
- User doesn't know what campaign type or objective to use

Default: simple for straightforward requests, pro for unclear/complex ones.
Do NOT ask "which mode do you want?" — decide yourself.

FORMAT: Your response MUST start with one of these markers (alone on a line):
===MODE:SIMPLE===
or
===MODE:PRO===

After the marker, write the appropriate opening message and first questions.
If SIMPLE: "Jasne — lecimy szybko. Zbieram tylko minimum do odpalenia kampanii." + 6 questions
If PRO: "Jasne — zrobimy pełny setup. Najpierw zbiorę podstawy." + 5 Stage 1 questions

If user already provided data in their message — do NOT re-ask, confirm and ask only for missing fields.
"""

_META_SIMPLE_TRIGGERS = {"simple", "szybka", "quick", "prosta", "szybko", "szybki"}
_META_PRO_TRIGGERS = {"pro", "full", "strategy", "szczegolowo", "szczegółowo", "pelny", "pełny"}


def _meta_wizard_post(user_id: str, text: str):
    """Post Meta wizard message to source_channel in wizard thread."""
    wizard = _ctx.meta_campaign_wizard.get(user_id)
    if not wizard:
        return
    ch = wizard.get("source_channel")
    ts = wizard.get("thread_ts")
    try:
        app.client.chat_postMessage(channel=ch, thread_ts=ts, text=text)
    except Exception as e:
        logger.error("_meta_wizard_post error: %s", e)


@app.command("/kampaniameta")
def handle_kampaniameta_slash(ack, command, logger):
    ack()
    user_id = command["user_id"]
    source_channel = command.get("channel_id", "")
    cmd_text = (command.get("text") or "").strip()
    cmd_lower = cmd_text.lower()
    cmd_words = cmd_lower.split()

    explicit_simple = any(t in cmd_words for t in _META_SIMPLE_TRIGGERS)
    explicit_pro = any(t in cmd_words for t in _META_PRO_TRIGGERS)

    if explicit_simple:
        mode = "simple"
    elif explicit_pro:
        mode = "pro"
    else:
        mode = "auto"

    extra_context = cmd_text
    for t in (_META_SIMPLE_TRIGGERS | _META_PRO_TRIGGERS):
        extra_context = re.sub(rf'\b{re.escape(t)}\b', '', extra_context, flags=re.IGNORECASE).strip()

    if mode == "simple":
        intro = (
            "⚡ *Szybka kampania Meta Ads — tryb SIMPLE*\n"
            "Zbierzemy tylko najważniejsze dane i jedziemy.\n"
            "Napisz `anuluj` żeby przerwać, `pro` żeby przejść w pełny tryb.\n\n"
            "Potrzebuję kilku rzeczy:\n\n"
            "1️⃣ Cel kampanii? _(leady / sprzedaż / ruch / zaangażowanie / wyświetlenia video / wiadomości)_\n"
            "2️⃣ Budżet dzienny?\n"
            "3️⃣ Kraj / lokalizacja?\n"
            "4️⃣ Link docelowy?\n"
            "5️⃣ Kreacja — wyślij plik (obraz/video) lub opisz co masz\n"
            "6️⃣ Tekst reklamy + nagłówek + CTA"
        )
    elif mode == "pro":
        intro = (
            "🟣 *Tworzymy kampanię Meta Ads — tryb PRO*\n"
            "Przeprowadzę Cię przez pełny profesjonalny setup.\n"
            "Napisz `anuluj` żeby przerwać w dowolnym momencie.\n\n"
            "Zaczynamy od podstaw:\n\n"
            "1️⃣ Co reklamujemy? _(produkt / usługa / oferta)_\n"
            "2️⃣ Jaki jest główny cel biznesowy?\n"
            "3️⃣ Kto jest idealnym klientem?\n"
            "4️⃣ Na jaki rynek kierujemy? _(kraj / miasta)_\n"
            "5️⃣ Cel kampanii? _(leady / sprzedaż / ruch / zaangażowanie / wiadomości / instalacje / video views)_\n"
            "6️⃣ Budżet dzienny lub miesięczny?"
        )
    else:  # auto
        intro = (
            "🟣 *Tworzymy kampanię Meta Ads!*\n"
            "Napisz `anuluj` żeby przerwać w dowolnym momencie.\n\n"
            "Powiedz mi co chcesz zrobić — dopasuję proces do potrzeb.\n"
            "Możesz podać od razu dane (cel, budżet, link, kreacja),\n"
            "albo opisać cel i pomogę zaprojektować kampanię."
        )

    try:
        resp = app.client.chat_postMessage(channel=source_channel, text=intro)
        thread_ts = resp["ts"]
    except Exception as e:
        logger.error("/kampaniameta post error: %s", e)
        return

    messages = [{"role": "assistant", "content": intro}]
    if extra_context:
        messages.append({"role": "user", "content": extra_context})

    _ctx.meta_campaign_wizard[user_id] = {
        "messages": messages,
        "source_channel": source_channel,
        "thread_ts": thread_ts,
        "mode": mode,
        "resolved_mode": mode if mode != "auto" else "",
        "files": [],
    }
    _ctx.save_wizard_state()

    # Track thread so follow-ups work even if wizard state is lost
    _ctx.bot_threads.add((source_channel, thread_ts))
    # Clear old conversation history — prevent data leaking from previous campaigns
    _ctx.conversation_history.pop(user_id, None)

    if extra_context:
        def _init_say(text):
            app.client.chat_postMessage(channel=source_channel, thread_ts=thread_ts, text=text)
        _handle_meta_campaign_wizard(user_id, None, [], _init_say)

    logger.info("/kampaniameta (%s) started by %s in %s", mode, user_id, source_channel)


logger.info("✅ /kampaniameta handler zarejestrowany")


def _meta_wizard_json_to_params(wjson: dict, wizard: dict) -> dict:
    """Convert /kampaniameta wizard completion JSON to create_campaign_draft params."""
    mode = wjson.get("mode", "simple")

    # ── Detect client_name from name/url/context ─────────────────────────────
    _name_raw = (wjson.get("campaign_name") or wjson.get("client_name") or "").lower()
    _url_raw  = (wjson.get("landing_page_url") or wjson.get("website_url") or "").lower()
    # Exclude agency's own domain from client detection
    _url_for_client = "" if "patoagencja" in _url_raw else _url_raw
    client_name = None
    # Check explicit client_name field first
    _explicit = (wjson.get("client_name") or "").lower().strip()
    if _explicit:
        for _k in ("dre", "drzwi dre", "instax", "m2", "pato"):
            if _k in _explicit or _explicit in _k:
                client_name = "drzwi dre" if _k in ("dre", "drzwi dre") else _k
                break
    # Then campaign name, then URL (agency domain excluded)
    if not client_name:
        for _k, _aliases in (
            ("dre", ("dre", "drzwi")),
            ("instax", ("instax",)),
            ("m2", ("m2",)),
            ("pato", ("pato",)),
        ):
            if any(a in _name_raw for a in _aliases) or any(a in _url_for_client for a in _aliases):
                client_name = _k
                break
    if not client_name:
        logger.warning("_meta_wizard_json_to_params: cannot detect client_name from wjson=%s", list(wjson.keys()))
        client_name = None  # Caller must handle: ask user

    # ── Budget ────────────────────────────────────────────────────────────────
    _budget_raw = wjson.get("daily_budget") or wjson.get("budget_daily") or "10"
    try:
        daily_budget = float(re.sub(r"[^\d.,]", "", str(_budget_raw)).replace(",", "."))
    except Exception:
        daily_budget = 10.0

    # ── Objective ─────────────────────────────────────────────────────────────
    _OBJ_MAP = {
        "TRAFFIC": "OUTCOME_TRAFFIC", "LEADS": "OUTCOME_LEADS",
        "SALES": "OUTCOME_SALES", "ENGAGEMENT": "OUTCOME_ENGAGEMENT",
        "AWARENESS": "OUTCOME_AWARENESS", "APP_PROMOTION": "OUTCOME_APP_PROMOTION",
        "MESSAGES": "OUTCOME_ENGAGEMENT", "VIDEO_VIEWS": "OUTCOME_ENGAGEMENT",
    }
    _obj_raw = (wjson.get("objective") or "TRAFFIC").upper().replace("OUTCOME_", "")
    objective = _OBJ_MAP.get(_obj_raw, "OUTCOME_TRAFFIC" if _obj_raw not in _OBJ_MAP else f"OUTCOME_{_obj_raw}")
    if not objective.startswith("OUTCOME_"):
        objective = "OUTCOME_TRAFFIC"

    # ── Targeting (build_meta_targeting expects gender=string, not genders=list) ──
    _age = (wjson.get("age_range") or "18-65")
    _age_parts = re.findall(r"\d+", str(_age))
    age_min = int(_age_parts[0]) if _age_parts else 18
    age_max = int(_age_parts[1]) if len(_age_parts) > 1 else 65

    _gender_raw = (wjson.get("gender") or "all").lower()
    if any(k in _gender_raw for k in ("kobi", "female", "women", "kobieta")):
        gender_str = "female"
    elif any(k in _gender_raw for k in ("mężcz", "mezcz", "male", "men", "mężczyzn")):
        gender_str = "male"
    else:
        gender_str = "all"

    # Locations: simple mode uses "country", pro mode uses "location" list
    if mode == "simple":
        _locs = [wjson.get("country") or "Polska"]
    else:
        _locs = wjson.get("location") or [wjson.get("country") or "Polska"]
        if isinstance(_locs, str):
            _locs = [_locs]

    interests = wjson.get("interests") or []
    if not interests and mode == "pro":
        for _aud in (wjson.get("audiences") or []):
            if isinstance(_aud, dict):
                interests += _aud.get("interests", [])
            elif isinstance(_aud, str):
                interests.append(_aud)

    # ── Creative text ─────────────────────────────────────────────────────────
    _creative = wjson.get("creative") or {}
    ad_copy  = _creative.get("primary_text") or (_creative.get("texts") or [None])[0] or ""
    _cta_raw = (_creative.get("cta") or "LEARN_MORE").upper().replace(" ", "_")
    # Normalize CTA to known Meta values
    _CTA_MAP = {
        "ODWIEDŹ_STRONĘ": "LEARN_MORE", "ODWIEDZ_STRONE": "LEARN_MORE",
        "DOWIEDZ_SIĘ_WIĘCEJ": "LEARN_MORE", "DOWIEDZ_SIE_WIECEJ": "LEARN_MORE",
        "KUP_TERAZ": "SHOP_NOW", "ZAPISZ_SIĘ": "SIGN_UP", "ZAPISZ_SIE": "SIGN_UP",
        "KONTAKT": "CONTACT_US", "WIADOMOŚĆ": "MESSAGE_PAGE", "WIADOMOSC": "MESSAGE_PAGE",
    }
    cta = _CTA_MAP.get(_cta_raw, _cta_raw)
    if cta not in ("LEARN_MORE", "SHOP_NOW", "SIGN_UP", "GET_QUOTE", "CONTACT_US",
                   "BOOK_TRAVEL", "MESSAGE_PAGE", "SUBSCRIBE", "DOWNLOAD"):
        cta = "LEARN_MORE"

    # ── Placements ────────────────────────────────────────────────────────────
    _placements = wjson.get("placements") or wjson.get("publisher_platforms")
    publisher_platforms = None
    placement_positions = None
    if isinstance(_placements, list) and _placements:
        _pl_lower = [p.lower() for p in _placements]
        if "instagram" in _pl_lower and "facebook" not in _pl_lower:
            publisher_platforms = ["instagram"]
        elif "facebook" in _pl_lower and "instagram" not in _pl_lower:
            publisher_platforms = ["facebook"]

    # ── Dates ─────────────────────────────────────────────────────────────────
    _sched = wjson.get("schedule") or {}
    _start_raw = _sched.get("start_date") or wjson.get("start_date") or ""
    _end_raw   = _sched.get("end_date")   or wjson.get("end_date")   or None
    # Normalize date format to YYYY-MM-DD
    def _norm_date(d):
        if not d:
            return None
        d = str(d).strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", d):
            return d[:10]
        _dm = re.match(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?", d)
        if _dm:
            y = _dm.group(3) or datetime.now().strftime("%Y")
            return f"{y}-{int(_dm.group(2)):02d}-{int(_dm.group(1)):02d}"
        return None
    start_date = _norm_date(_start_raw) or datetime.now().strftime("%Y-%m-%d")
    end_date   = _norm_date(_end_raw)

    return {
        "client_name":         client_name,
        "campaign_name":       wjson.get("campaign_name") or f"Kampania {client_name.upper()} — {datetime.now().strftime('%d.%m.%Y')}",
        "objective":           objective,
        "daily_budget":        daily_budget,
        "website_url":         _url_raw or "",
        "ad_copy":             ad_copy,
        "call_to_action":      cta,
        "cta_enabled":         bool(ad_copy),
        "link_enabled":        bool(_url_raw),
        "publisher_platforms": publisher_platforms,
        "placement_positions": placement_positions,
        "start_date":          start_date,
        "end_date":            end_date,
        "targeting": {
            "gender":    gender_str,
            "age_min":   age_min,
            "age_max":   age_max,
            "interests": interests,
            "locations": _locs,
        },
    }


_LAUNCH_KEYWORDS = {"uruchom", "uruchamiaj", "zatwierdź", "zatwierdz", "launch", "tak", "yes", "ok", "go", "lecimy", "wgraj", "deploy"}
_CANCEL_KEYWORDS = {"anuluj", "cancel", "stop", "przerwij", "nie", "no", "rezygnuję", "rezygnuje"}


def _handle_meta_campaign_wizard(user_id: str, user_message: str | None, files: list, say_fn) -> bool:
    """
    Handle a channel thread reply for a user in the /kampaniameta wizard.
    Claude-driven with AUTO/SIMPLE/PRO modes.
    """
    if user_id not in _ctx.meta_campaign_wizard:
        return False

    wizard = _ctx.meta_campaign_wizard[user_id]
    current_mode = wizard.get("resolved_mode") or wizard.get("mode", "auto")

    # ── AWAITING APPROVAL STATE: draft already created, waiting for launch/cancel ──
    if wizard.get("state") == "awaiting_approval":
        if user_message is not None:
            msg_lower = user_message.strip().lower()
            _words = set(re.split(r"\W+", msg_lower))
            if _words & _LAUNCH_KEYWORDS:
                camp_id = wizard.get("draft_campaign_id")
                del _ctx.meta_campaign_wizard[user_id]
                _ctx.save_wizard_state()
                say_fn("🚀 Uruchamiam kampanię...")
                say_fn(approve_and_launch_campaign(camp_id))
                return True
            if _words & _CANCEL_KEYWORDS:
                camp_id = wizard.get("draft_campaign_id")
                del _ctx.meta_campaign_wizard[user_id]
                _ctx.save_wizard_state()
                say_fn("❌ Kampania anulowana.")
                cancel_campaign_draft(camp_id)
                return True
            # User wants to change something — show reminder
            say_fn(
                "✏️ Aby wprowadzić zmiany uruchom `/kampaniameta` od nowa.\n"
                "Napisz *uruchom* żeby uruchomić ten draft lub *anuluj* żeby go usunąć."
            )
        return True

    # ── ASKING CLIENT STATE: waiting for user to specify which client ─────────
    if wizard.get("state") == "asking_client":
        if user_message is not None:
            _cl_msg = user_message.strip().lower()
            _detected_client = None
            for _k, _aliases in (
                ("dre", ("dre", "drzwi")),
                ("instax", ("instax",)),
                ("m2", ("m2",)),
                ("pato", ("pato",)),
            ):
                if any(a in _cl_msg for a in _aliases):
                    _detected_client = _k
                    break
            if _detected_client:
                _pending_wjson = wizard.get("pending_wjson")
                if _pending_wjson:
                    _pending_wjson["client_name"] = _detected_client
                    wizard.pop("state", None)
                    wizard.pop("pending_wjson", None)
                    _account_id = get_meta_account_id(_detected_client)
                    if not _account_id:
                        say_fn(f"⚠️ Nie znalazłem konta Meta dla klienta `{_detected_client}`. Sprawdź konfigurację.")
                        del _ctx.meta_campaign_wizard[user_id]
                        _ctx.save_wizard_state()
                        return True
                    try:
                        _params = _meta_wizard_json_to_params(_pending_wjson, wizard)
                        _params["client_name"] = _detected_client  # ensure correct client
                        say_fn("📋 Tworzę szkic kampanii w Meta Ads...")
                        _targeting = build_meta_targeting(_params.get("targeting") or {})
                        _creatives_raw = wizard.get("files", [])
                        _creatives = []
                        if _creatives_raw:
                            say_fn(f"🎨 Uploaduję {len(_creatives_raw)} kreacji do Meta...")
                            for _fname, _fdata, _fmime in _creatives_raw:
                                try:
                                    _cr = upload_creative_to_meta(_account_id, _fdata, _fmime, _fname)
                                    _creatives.append(_cr)
                                except Exception as _uce:
                                    say_fn(f"⚠️ Nie udało się uploadować `{_fname}`: {_uce}")
                        _draft = create_campaign_draft(_account_id, _params, _creatives, _targeting)
                        _tgt_preview = {
                            "gender":    _params["targeting"]["gender"],
                            "age_min":   _params["targeting"]["age_min"],
                            "age_max":   _params["targeting"]["age_max"],
                            "locations": _params["targeting"]["locations"],
                            "interests": _params["targeting"]["interests"],
                        }
                        _preview = generate_campaign_preview(_params, _tgt_preview, len(_creatives), _draft)
                        say_fn(_preview)
                        say_fn("✅ Kampania jest w Meta Ads — *wyłączona*. Włącz ją ręcznie w panelu kiedy będziesz gotowy.")
                        del _ctx.meta_campaign_wizard[user_id]
                        _ctx.save_wizard_state()
                    except Exception as _de:
                        logger.error("Meta wizard (asking_client) draft creation error: %s", _de, exc_info=True)
                        say_fn(f"❌ Błąd tworzenia draftu: {_de}")
                        del _ctx.meta_campaign_wizard[user_id]
                        _ctx.save_wizard_state()
                else:
                    del _ctx.meta_campaign_wizard[user_id]
                    _ctx.save_wizard_state()
                    say_fn("❌ Nie mam danych kampanii. Uruchom `/kampaniameta` od nowa.")
            else:
                say_fn(
                    "❓ Nie rozpoznałem klienta. Podaj jeden z: *dre*, *instax*, *m2*, *pato*\n"
                    "Np. napisz: `dre` lub `drzwi dre`"
                )
        return True

    if user_message is not None:
        msg_lower = user_message.strip().lower()

        if msg_lower in ("anuluj", "cancel", "stop", "przerwij"):
            del _ctx.meta_campaign_wizard[user_id]
            _ctx.save_wizard_state()
            say_fn("❌ Tworzenie kampanii Meta Ads anulowane.")
            return True

        if msg_lower in ("pro", "full", "szczegolowo", "szczegółowo") and current_mode == "simple":
            wizard["resolved_mode"] = "pro"
            wizard["messages"].append({"role": "user", "content": "Chcę przejść w pełny tryb PRO."})
            wizard["messages"].append({"role": "assistant", "content": (
                "🟣 OK — przechodzimy w tryb PRO. Teraz zrobię pełny setup kampanii.\n"
                "Kontynuuję z danymi które już mam."
            )})
            say_fn("🟣 OK — przechodzimy w tryb PRO. Teraz zrobię pełny setup kampanii.\nKontynuuję z danymi które już mam.")
            return True

        # Build message content with file info
        content = user_message
        if files:
            file_names = [f.get("name", "plik") for f in files]
            content += f"\n[Załączone pliki: {', '.join(file_names)}]"
            # Download files for later use
            _file_ids = [f["id"] for f in files]
            downloaded = download_slack_files(_file_ids) if _file_ids else []
            wizard.setdefault("files", []).extend(downloaded)

        wizard["messages"].append({"role": "user", "content": content})

    # Select system prompt
    if current_mode == "auto":
        system_prompt = META_CAMPAIGN_AUTO_PROMPT
    elif current_mode == "simple":
        system_prompt = META_CAMPAIGN_SIMPLE_PROMPT
    else:
        system_prompt = META_CAMPAIGN_PRO_PROMPT

    try:
        response = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system=system_prompt,
            messages=wizard["messages"],
        )
        assistant_text = response.content[0].text

        # AUTO MODE: resolve mode
        if current_mode == "auto":
            if "===MODE:SIMPLE===" in assistant_text:
                wizard["resolved_mode"] = "simple"
                assistant_text = assistant_text.replace("===MODE:SIMPLE===", "").strip()
                logger.info("Meta wizard AUTO → SIMPLE for user %s", user_id)
            elif "===MODE:PRO===" in assistant_text:
                wizard["resolved_mode"] = "pro"
                assistant_text = assistant_text.replace("===MODE:PRO===", "").strip()
                logger.info("Meta wizard AUTO → PRO for user %s", user_id)
            else:
                wizard["resolved_mode"] = "pro"
                logger.warning("Meta wizard AUTO: no mode marker, defaulting to PRO for user %s", user_id)

        # Detect SIMPLE→PRO switch
        if "===SWITCH:PRO===" in assistant_text:
            wizard["resolved_mode"] = "pro"
            assistant_text = assistant_text.replace("===SWITCH:PRO===", "").strip()
            logger.info("Meta wizard SIMPLE → PRO switch for user %s", user_id)

        wizard["messages"].append({"role": "assistant", "content": assistant_text})

        if len(wizard["messages"]) > 30:
            wizard["messages"] = wizard["messages"][-30:]

        if "===KAMPANIA_META_GOTOWA===" in assistant_text:
            clean_text = assistant_text.replace("===KAMPANIA_META_GOTOWA===", "").strip()
            say_fn(clean_text)
            # Robustny parsing JSON — obsłuż ```json ... ```, ``` ... ``` i plain {...}
            _wjson = None
            for _pattern in (
                r"```json\s*(\{[\s\S]*?\})\s*```",
                r"```\s*(\{[\s\S]*?\})\s*```",
                r"(\{[\s\S]*\"mode\"[\s\S]*\})",
            ):
                _m = re.search(_pattern, assistant_text)
                if _m:
                    try:
                        _wjson = json.loads(_m.group(1))
                        break
                    except json.JSONDecodeError:
                        continue
            if _wjson:
                try:
                    _params = _meta_wizard_json_to_params(_wjson, wizard)
                    _detected_client = _params.get("client_name")
                    _account_id = get_meta_account_id(_detected_client or "") if _detected_client else ""
                    if not _detected_client or not _account_id:
                        # Can't auto-detect client — ask user instead of silently failing
                        wizard["state"] = "asking_client"
                        wizard["pending_wjson"] = _wjson
                        _ctx.save_wizard_state()
                        _reason = f"klient `{_detected_client}` nie ma skonfigurowanego konta Meta" if (_detected_client and not _account_id) else "nie rozpoznałem klienta"
                        say_fn(
                            f"❓ {_reason.capitalize()}.\n"
                            f"Napisz dla kogo kampania: *dre*, *instax*, *m2* lub *pato*"
                        )
                        return True
                    say_fn("📋 Tworzę szkic kampanii w Meta Ads...")
                    _targeting = build_meta_targeting(_params.get("targeting") or {})
                    _creatives_raw = wizard.get("files", [])
                    _creatives = []
                    if _creatives_raw:
                        say_fn(f"🎨 Uploaduję {len(_creatives_raw)} kreacji do Meta...")
                        for _fname, _fdata, _fmime in _creatives_raw:
                            try:
                                _cr = upload_creative_to_meta(_account_id, _fdata, _fmime, _fname)
                                _creatives.append(_cr)
                            except Exception as _uce:
                                say_fn(f"⚠️ Nie udało się uploadować `{_fname}`: {_uce}")
                    _draft = create_campaign_draft(_account_id, _params, _creatives, _targeting)
                    # generate_campaign_preview expects targeting in build_meta_targeting format
                    _tgt_preview = {
                        "gender":    _params["targeting"]["gender"],
                        "age_min":   _params["targeting"]["age_min"],
                        "age_max":   _params["targeting"]["age_max"],
                        "locations": _params["targeting"]["locations"],
                        "interests": _params["targeting"]["interests"],
                    }
                    _preview = generate_campaign_preview(_params, _tgt_preview, len(_creatives), _draft)
                    say_fn(_preview)
                    say_fn("✅ Kampania jest w Meta Ads — *wyłączona*. Włącz ją ręcznie w panelu kiedy będziesz gotowy.")
                    del _ctx.meta_campaign_wizard[user_id]
                    _ctx.save_wizard_state()
                except Exception as _de:
                    logger.error("Meta wizard draft creation error: %s", _de, exc_info=True)
                    say_fn(f"❌ Błąd tworzenia draftu: {_de}")
                    del _ctx.meta_campaign_wizard[user_id]
                    _ctx.save_wizard_state()
            else:
                logger.warning("Meta wizard: no JSON found in completion response — deleting wizard")
                del _ctx.meta_campaign_wizard[user_id]
                _ctx.save_wizard_state()
        else:
            say_fn(assistant_text)

    except Exception as e:
        logger.error("Meta campaign wizard Claude error: %s", e)
        say_fn(f"❌ Błąd komunikacji z AI: {e}")

    return True


# ── /kampaniagoogle — Claude-driven Google Ads wizard ─────────────────────────

GOOGLE_CAMPAIGN_SYSTEM_PROMPT = """\
⛔ ABSOLUTNY ZAKAZ — przeczytaj zanim cokolwiek napiszesz:
NIGDY nie pisz: "Wgrywam", "Uruchamiam", "Kampania będzie live", "Za X minut",
"Ruszam z uruchomieniem", "Ktoś z teamu musi wdrożyć", "Przekaż brief zespołowi", ani niczego podobnego.
NIGDY nie pisz sekcji "CO DALEJ", "Gotowe!", "Kampania rozpocznie się", "Spodziewaj się kliknięć",
"Monitoring: Sprawdzę performance", "Kampania jest gotowa", ani żadnego tekstu sugerującego że kampania
już istnieje lub zaraz ruszy — jeśli NIE wygenerowałeś JSON z markerem ===KAMPANIA_GOOGLE_GOTOWA===.
KLUCZOWE: Dopóki nie wygenerujesz JSON z markerem ===KAMPANIA_GOOGLE_GOTOWA=== — kampania NIE istnieje
w Google Ads. Zero. Żadna. System tworzy ją dopiero gdy dostanie ten marker + poprawny JSON.
Pisząc "Gotowe!" bez JSON kłamiesz użytkownikowi — to jest niedopuszczalne.
Po wygenerowaniu JSON ze znacznikiem ===KAMPANIA_GOOGLE_GOTOWA=== — SYSTEM tworzy kampanię AUTOMATYCZNIE
i sam wyśle potwierdzenie z linkiem. Twoja rola kończy się na JSON — nie dodawaj nic po nim.
⛔ KONIEC ZAKAZU

Jesteś ekspertem Google Ads i asystentem do tworzenia kampanii reklamowych w Slacku (Sebol).

KRYTYCZNA ZASADA: NIGDY nie wymyślaj, nie zakładaj i nie uzupełniaj danych których użytkownik NIE podał.
Każda sesja wizarda zaczyna się od zera — nie kopiuj danych z poprzednich rozmów.

Twoje zachowanie:
1. Po rozpoczęciu procesu prowadzisz rozmowę etapami.
2. Najpierw ustalasz typ kampanii i cel biznesowy.
3. Następnie zadajesz szczegółowe pytania zależne od typu kampanii.
4. Nie zakładasz niczego samodzielnie, jeśli użytkownik tego nie potwierdził.
5. Zawsze wykrywasz braki, niejasności, sprzeczności i dopytujesz.
6. Kończysz dopiero wtedy, gdy masz pełen komplet informacji operacyjnych.

Styl prowadzenia rozmowy:
- Krótko, jasno, etapami, bez lania wody, ale bardzo konkretnie.
- Po polsku, naturalnie — jak kolega z agencji.
- Zadawaj pytania w partiach po kilka (3–6), nie 40 naraz.
- Jeśli odpowiedź jest zbyt ogólna — dopytuj.
- Jeśli coś jest sprzeczne — wytknij i poproś o doprecyzowanie.

INTERAKTYWNE OPCJE:
Gdy pytanie ma zestaw typowych odpowiedzi (np. typ kampanii, cel, strategia biddingowa, kraj),
dołącz tag [OPCJE: opcja1 | opcja2 | opcja3] bezpośrednio po treści pytania (max 5 opcji, każda maks 20 znaków).
Użytkownik będzie mógł kliknąć przycisk LUB wpisać własną odpowiedź w wątku.
NIE dodawaj opcji do pytań o wolny tekst (nagłówki, opisy, URL, nazwy brand).
Przykłady:
- "Jaki typ kampanii? [OPCJE: Search | Performance Max | YouTube | Display | Shopping]"
- "Jaki budżet dobowy? [OPCJE: 50 PLN | 100 PLN | 200 PLN | 500 PLN]"
- "Cel kampanii? [OPCJE: Sprzedaż | Leady | Ruch | Świadomość]"
- "Strategia biddingowa? [OPCJE: Maks. kliknięcia | Maks. konwersje | CPA | ROAS | Ręczny CPC]"

Typy kampanii: Search, Performance Max, Display, Video/YouTube, Demand Gen, Shopping, App.
Jeśli user nie wie jaki typ — zrób diagnozę i zaproponuj.

Etapy rozmowy:
RUNDA 1 — cel, oferta, rynek, budżet, typ kampanii
RUNDA 2 — landing page, odbiorcy, tracking, KPI, harmonogram
RUNDA 3 — pytania zależne od typu kampanii (słowa kluczowe/feed/kreacje/odbiorcy)
RUNDA 4 — brakujące elementy kreatywne, techniczne, wykluczenia
RUNDA 5 — finalne podsumowanie i potwierdzenie kompletności

Wymagane pola przed finalizacją:
- typ kampanii, cel, budżet, lokalizacja, język, konwersja/KPI
- URL docelowy, targetowanie/słowa kluczowe/odbiorcy
- materiały reklamowe, wykluczenia, harmonogram

WAŻNE — Sygnał zakończenia:
Gdy masz KOMPLET danych i użytkownik potwierdzi, wygeneruj odpowiedź z DOKŁADNIE takim znacznikiem:
===KAMPANIA_GOOGLE_GOTOWA===
A pod nim 4 sekcje:
1. **Podsumowanie kampanii** — typ, cel, oferta, grupa docelowa, rynek, budżet, KPI, start
2. **Struktura kampanii** — kampania, grupy reklam/asset groups, słowa kluczowe/odbiorcy/produkty, reklamy/assets, rozszerzenia
3. **Ryzyka / checklista** — braki, ryzyka, rekomendacje, elementy do weryfikacji technicznej
4. **Dane do utworzenia kampanii — JSON**:
```json
{
  "campaign_name": "", "campaign_type": "", "business_goal": "",
  "conversion_goal": "", "brand_name": "", "website_url": "",
  "landing_page_url": "", "country": "", "locations": [],
  "languages": [], "daily_budget": "", "monthly_budget": "",
  "bidding_strategy": "", "target_cpa": "", "target_roas": "",
  "start_date": "", "end_date": "", "audiences": [],
  "remarketing_lists": [], "customer_match": false,
  "keywords": [], "negative_keywords": [],
  "placements": [], "excluded_placements": [],
  "product_feed": {"merchant_center": false, "feed_available": false,
    "feed_scope": "", "included_products": [], "excluded_products": []},
  "asset_groups": [],
  "ads": {"headlines": [], "long_headlines": [], "descriptions": [],
    "paths": [], "call_to_action": "", "images": [], "logos": [], "videos": []},
  "extensions": {"sitelinks": [], "callouts": [], "structured_snippets": [],
    "phone": "", "location_extension": false, "promotion_extension": []},
  "schedule": {"ad_schedule": [], "devices": [], "frequency_cap": ""},
  "tracking": {"ga4": false, "gtm": false, "google_tag": false,
    "conversion_tracking": false, "conversion_actions": []},
  "compliance": {"legal_restrictions": [], "brand_restrictions": [],
    "excluded_terms": [], "excluded_brands": []},
  "notes": [], "missing_items": [], "ready_to_create": false
}
```

Pytania obowiązkowe niezależnie od typu:
1. Firma/marka, 2. Produkt/usługa, 3. Cel kampanii, 4. Najważniejsza konwersja,
5. Budżet, 6. Rynek docelowy, 7. Landing page, 8. Oferta/przewaga,
9. Odbiorca, 10. Materiały kreatywne, 11. Ograniczenia brandowe/prawne,
12. KPI sukcesu, 13. Tracking/konwersje gotowe?, 14. Data startu,
15. Data zakończenia?, 16. Listy remarketingowe?, 17. Wykluczenia?,
18. Pełna automatyzacja czy kontrolowana struktura?

Szczegółowe pytania per typ kampanii:
- Search: brand/niebrand, strategia stawek, frazy (główne/poboczne/long-tail/wykluczenia), dopasowania, RSA (10-15 nagłówków, 4 opisy, ścieżki URL), rozszerzenia, harmonogram
- Performance Max: feed/Merchant Center, asset groups (nagłówki/opisy/obrazy/logo/video), audience signals, URL expansion
- Display: prospecting/remarketing, segmenty, placementy, responsive display ads, częstotliwość
- Video/YouTube: materiały video, formaty, CPV/tCPA, inventory type, companion banner
- Demand Gen: custom segments, kreacje (poziome/pionowe/kwadratowe), asset groups
- Shopping: Merchant Center, feed, custom labels, podział (marka/kategoria/marża), priorytety
- App: platforma, link do sklepu, Firebase/SDK, eventy in-app, target CPI/CPA

Nigdy nie finalizuj po ogólnikowej odpowiedzi. Zawsze doprecyzowuj.
"""


GOOGLE_CAMPAIGN_SIMPLE_PROMPT = """\
⛔ ABSOLUTNY ZAKAZ — przeczytaj zanim cokolwiek napiszesz:
NIGDY nie pisz: "Wgrywam", "Uruchamiam", "Kampania będzie live", "Za X minut",
"Ruszam z uruchomieniem", "Ktoś z teamu musi wdrożyć", "Przekaż brief zespołowi", ani niczego podobnego.
NIGDY nie pisz sekcji "CO DALEJ", "Gotowe!", "Kampania rozpocznie się", "Spodziewaj się kliknięć",
"Monitoring: Sprawdzę performance", "Kampania jest gotowa", ani żadnego tekstu sugerującego że kampania
już istnieje lub zaraz ruszy — jeśli NIE wygenerowałeś JSON z markerem ===KAMPANIA_GOOGLE_GOTOWA===.
KLUCZOWE: Dopóki nie wygenerujesz JSON z markerem ===KAMPANIA_GOOGLE_GOTOWA=== — kampania NIE istnieje.
Pisząc "Gotowe!" bez JSON kłamiesz użytkownikowi — to jest niedopuszczalne.
Po wygenerowaniu JSON ze znacznikiem ===KAMPANIA_GOOGLE_GOTOWA=== — SYSTEM tworzy kampanię AUTOMATYCZNIE.
Nie mów użytkownikowi żeby cokolwiek robił ręcznie. Kampania powstaje sama. Ty kończysz na JSON.
⛔ KONIEC ZAKAZU

Jesteś ekspertem Google Ads w Slacku (Sebol). Działasz w trybie SIMPLE — szybkie kampanie.

KRYTYCZNA ZASADA: NIGDY nie wymyślaj danych których użytkownik NIE podał.
Jeśli brakuje targetowania, lokalizacji, grupy wiekowej — ZAPYTAJ, nie uzupełniaj sam.
Każda sesja zaczyna się od zera — nie kopiuj danych z poprzednich rozmów.

Zasady:
- Zbieraj TYLKO krytyczne dane — nie rób pełnego audytu.
- Zadaj wszystkie pytania w jednej rundzie (maks 7 pytań).
- Jeśli user podał już dane w pierwszej wiadomości — nie pytaj ponownie.
- Dopytuj TYLKO jeśli brakuje: budżetu, materiału reklamowego, URL lub targetowania.
- Po polsku, naturalnie, krótko.

INTERAKTYWNE OPCJE:
Gdy pytanie ma typowe gotowe odpowiedzi, dołącz [OPCJE: opcja1 | opcja2 | opcja3] po pytaniu (max 5 opcji, maks 20 znaków każda).
NIE dodawaj opcji do pytań o wolny tekst (URL, nagłówki, opisy).
Przykłady: "Typ kampanii? [OPCJE: Search | Performance Max | YouTube | Display | Shopping]"
"Cel? [OPCJE: Sprzedaż | Leady | Ruch | Świadomość]"

Minimalne dane per typ kampanii:

Search:
1. budżet, 2. kraj/lokalizacja, 3. landing page, 4. 3-10 słów kluczowych, 5. 3 nagłówki, 6. 1-2 opisy

Performance Max:
1. budżet, 2. kraj, 3. landing page, 4. nagłówki, 5. opisy, 6. obrazy/video (opcjonalne)

Video / YouTube:
1. link do filmu, 2. landing page, 3. budżet dzienny, 4. kraj/lokalizacja,
5. wiek odbiorców, 6. zainteresowania (opcjonalne), 7. CTA

Display:
1. budżet, 2. lokalizacja, 3. landing page, 4. odbiorcy/segmenty, 5. kreacje (obrazy), 6. nagłówki+opisy

Demand Gen:
1. budżet, 2. kraj, 3. landing page, 4. kreacje (obrazy/video), 5. nagłówki+opisy, 6. odbiorcy

Shopping:
1. budżet, 2. kraj, 3. Merchant Center aktywne?, 4. zakres produktów, 5. target ROAS (opcjonalny)

Jeśli user poda od razu dużo danych (np. "kampania yt budżet 50 zł Polska 18-34 film: link strona: link")
— NIE pytaj więcej, od razu generuj output.

PRZEŁĄCZENIE NA PRO:
Jeśli w trakcie rozmowy okaże się że temat jest złożony (user nie wie czego chce, chce strategii,
ma 2+ cele/segmenty, pyta o ROAS/CPA/remarketing/segmentację), ZACZNIJ odpowiedź od:
===SWITCH:PRO===
Tu już wchodzimy w bardziej rozbudowany setup. Przełączam na tryb PRO, żeby dobrze dobrać strukturę.

Jeśli user sam napisze "pro" — nie musisz nic robić, system obsłuży to automatycznie.

WAŻNE — Sygnał zakończenia:
Gdy masz wystarczające dane, wygeneruj odpowiedź z DOKŁADNIE takim znacznikiem:
===KAMPANIA_GOOGLE_GOTOWA===
A pod nim 3 sekcje:
1. **Podsumowanie** — typ, budżet, lokalizacja, targetowanie, link docelowy
2. **Struktura kampanii** — krótka
3. **JSON** — taki sam format jak w trybie PRO:
```json
{
  "campaign_name": "", "campaign_type": "", "business_goal": "",
  "conversion_goal": "", "brand_name": "", "website_url": "",
  "landing_page_url": "", "country": "", "locations": [],
  "languages": [], "daily_budget": "", "monthly_budget": "",
  "bidding_strategy": "", "target_cpa": "", "target_roas": "",
  "start_date": "", "end_date": "", "audiences": [],
  "remarketing_lists": [], "customer_match": false,
  "keywords": [], "negative_keywords": [],
  "placements": [], "excluded_placements": [],
  "product_feed": {"merchant_center": false, "feed_available": false,
    "feed_scope": "", "included_products": [], "excluded_products": []},
  "asset_groups": [],
  "ads": {"headlines": [], "long_headlines": [], "descriptions": [],
    "paths": [], "call_to_action": "", "images": [], "logos": [], "videos": []},
  "extensions": {"sitelinks": [], "callouts": [], "structured_snippets": [],
    "phone": "", "location_extension": false, "promotion_extension": []},
  "schedule": {"ad_schedule": [], "devices": [], "frequency_cap": ""},
  "tracking": {"ga4": false, "gtm": false, "google_tag": false,
    "conversion_tracking": false, "conversion_actions": []},
  "compliance": {"legal_restrictions": [], "brand_restrictions": [],
    "excluded_terms": [], "excluded_brands": []},
  "notes": [], "missing_items": [], "ready_to_create": false
}
```
"""

_SIMPLE_TRIGGERS = {"simple", "szybka", "quick", "prosta", "szybko", "szybki"}
_PRO_TRIGGERS = {"pro", "full", "szczegolowo", "szczegółowo", "pelny", "pełny"}

GOOGLE_CAMPAIGN_AUTO_PROMPT = """\
⛔ ABSOLUTNY ZAKAZ — przeczytaj zanim cokolwiek napiszesz:
NIGDY nie pisz: "Wgrywam", "Uruchamiam", "Kampania będzie live", "Za X minut",
"Ruszam z uruchomieniem", "Ktoś z teamu musi wdrożyć", "Przekaż brief zespołowi", ani niczego podobnego.
NIGDY nie pisz sekcji "CO DALEJ", "Gotowe!", "Kampania rozpocznie się", "Spodziewaj się kliknięć",
"Monitoring: Sprawdzę performance", "Kampania jest gotowa", ani żadnego tekstu sugerującego że kampania
już istnieje lub zaraz ruszy — jeśli NIE wygenerowałeś JSON z markerem ===KAMPANIA_GOOGLE_GOTOWA===.
KLUCZOWE: Dopóki nie wygenerujesz JSON z markerem ===KAMPANIA_GOOGLE_GOTOWA=== — kampania NIE istnieje.
Pisząc "Gotowe!" bez JSON kłamiesz użytkownikowi — to jest niedopuszczalne.
Po wygenerowaniu JSON ze znacznikiem ===KAMPANIA_GOOGLE_GOTOWA=== — SYSTEM tworzy kampanię AUTOMATYCZNIE.
Nie mów użytkownikowi żeby cokolwiek robił ręcznie. Kampania powstaje sama. Ty kończysz na JSON.
⛔ KONIEC ZAKAZU

Jesteś ekspertem Google Ads w Slacku (Sebol). Twoja rola: analiza intencji użytkownika i wybór trybu pracy.

KRYTYCZNA ZASADA: NIGDY nie wymyślaj danych których użytkownik NIE podał.
Każda sesja zaczyna się od zera — nie kopiuj danych z poprzednich rozmów.

Użytkownik właśnie zaczął tworzenie kampanii Google Ads komendą /kampaniagoogle.
Przeanalizuj jego PIERWSZĄ wiadomość i zdecyduj, czy chce:
- **SIMPLE** — szybkie odpalenie prostej kampanii
- **PRO** — pełny, strategiczny setup

ZASADY WYBORU:

Wybierz SIMPLE jeśli:
- User chce "szybko", "prosto", "bez pytań", "minimum", "tylko odpal", "na szybko"
- User od razu podaje komplet danych: typ, budżet, lokalizacja, materiał, URL
- Prosty jednoelementowy setup (jeden film na YT, jedna kampania brand search, itp.)
- Sygnały: "wrzuć kampanię", "mam film i link", "zrób prostą kampanię"

Wybierz PRO jeśli:
- User nie wie jaki typ kampanii wybrać
- User chce rekomendacji, strategii, struktury
- Są 2+ cele, segmenty, produkty/usługi
- Sygnały: "strategia", "struktura", "dobierz", "zoptymalizuj", "ROAS", "CPA",
  "remarketing", "audience signals", "segmentacja", "rozpisz", "porządnie"
- User mówi o sklepie z feedem, wieloproduktowym PMax, brand/non-brand split

Domyślnie:
- Proste wejścia → SIMPLE
- Niejasne i złożone → PRO

NIE PYTAJ użytkownika "chcesz simple czy pro?" — sam zdecyduj.

FORMAT ODPOWIEDZI:
Twoja odpowiedź MUSI zaczynać się od jednego z tych znaczników (sam w linii):
===MODE:SIMPLE===
lub
===MODE:PRO===

Po znaczniku napisz odpowiedni komunikat startowy i PIERWSZE pytania.

Jeśli SIMPLE:
"Jasne — lecimy szybko. Zbieram tylko minimum potrzebne do odpalenia kampanii."
Potem minimalny zestaw pytań (6-7) zależny od tego co user już podał.

Jeśli PRO:
"Jasne — zrobimy pełny setup. Najpierw zbiorę podstawy kampanii, potem dopytam o szczegóły."
Potem 5 pytań startowych (cel, oferta, rynek, budżet, typ kampanii).

Jeśli user podał już dane — NIE pytaj o nie ponownie, po prostu potwierdź i pytaj o brakujące.
"""


def _build_wizard_blocks(user_id: str, text: str) -> list | None:
    """
    Parse Claude's response for [OPCJE: opt1 | opt2 | ...] tags and convert to Block Kit blocks.
    Returns list of blocks if any [OPCJE:] tag found, else None (fall back to plain text).
    Multiple [OPCJE:] groups (one per question) are supported.
    """
    import time
    pattern = re.compile(r"\[OPCJE:\s*([^\]]+)\]")
    if not pattern.search(text):
        return None

    blocks = []
    last_end = 0
    ts_base = int(time.time() * 1000)

    for i, match in enumerate(pattern.finditer(text)):
        # Text before this match (the question/paragraph) — section text max 3000 chars
        before = text[last_end:match.start()].strip()
        if before:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": before[:3000]},
            })
        last_end = match.end()

        # Parse options
        raw_opts = match.group(1)
        opts = [o.strip() for o in raw_opts.split("|") if o.strip()][:5]

        # Build button elements — action_id encodes user_id + option index
        elements = []
        for j, opt in enumerate(opts):
            # Slack action_id max 255 chars; button text max 75 chars; value max 2000 chars
            action_id = f"gw_btn_{user_id}_{ts_base}_{i}_{j}"[:255]
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": opt[:75], "emoji": False},
                "value": opt[:2000],
                "action_id": action_id,
            })

        blocks.append({
            "type": "actions",
            "block_id": f"gw_q_{user_id}_{ts_base}_{i}"[:255],
            "elements": elements,
        })

    # Text after last match — section text max 3000 chars
    tail = text[last_end:].strip()
    if tail:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": tail[:3000]},
        })

    # Footer hint
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "_Kliknij opcję lub wpisz własną odpowiedź w wątku_ ↓"}],
    })

    return blocks


def _google_wizard_post(user_id: str, text: str):
    """Post Google wizard message to source_channel in wizard thread.
    Automatically converts [OPCJE: ...] tags to interactive Block Kit buttons.
    """
    wizard = _ctx.google_campaign_wizard.get(user_id)
    if not wizard:
        return
    ch = wizard.get("source_channel")
    ts = wizard.get("thread_ts")
    try:
        blocks = _build_wizard_blocks(user_id, text)
        if blocks:
            try:
                app.client.chat_postMessage(
                    channel=ch, thread_ts=ts,
                    text=text[:3000],   # fallback for notifications
                    blocks=blocks,
                )
                return
            except Exception as blocks_err:
                logger.warning("_google_wizard_post blocks failed, falling back to plain text: %s", blocks_err)
        app.client.chat_postMessage(channel=ch, thread_ts=ts, text=text)
    except Exception as e:
        logger.error("_google_wizard_post error: %s", e)


@app.command("/kampaniagoogle")
def handle_kampaniagoogle_slash(ack, command, logger):
    ack()
    user_id = command["user_id"]
    source_channel = command.get("channel_id", "")
    cmd_text = (command.get("text") or "").strip()
    cmd_lower = cmd_text.lower()
    cmd_words = cmd_lower.split()

    # 1. Detect explicit mode from command args
    explicit_simple = any(t in cmd_words for t in _SIMPLE_TRIGGERS)
    explicit_pro = any(t in cmd_words for t in _PRO_TRIGGERS)

    if explicit_simple:
        mode = "simple"
    elif explicit_pro:
        mode = "pro"
    else:
        mode = "auto"

    # Strip mode triggers from text to get extra context
    extra_context = cmd_text
    for t in (_SIMPLE_TRIGGERS | _PRO_TRIGGERS):
        extra_context = re.sub(rf'\b{re.escape(t)}\b', '', extra_context, flags=re.IGNORECASE).strip()

    # Build intro based on resolved mode
    if mode == "simple":
        intro = (
            "⚡ *Szybka kampania Google Ads — tryb SIMPLE*\n"
            "Zbierzemy tylko najważniejsze dane i jedziemy.\n"
            "Napisz `anuluj` żeby przerwać, `pro` żeby przejść w pełny tryb.\n\n"
            "Potrzebuję kilku rzeczy:\n\n"
            "1️⃣ Jaki typ kampanii? _(Search / YouTube / PMax / Display / Demand Gen / Shopping)_\n"
            "2️⃣ Budżet dzienny lub miesięczny?\n"
            "3️⃣ Rynek — kraj / miasta?\n"
            "4️⃣ Cel kampanii? _(sprzedaż / leady / ruch / wyświetlenia)_\n"
            "5️⃣ Link docelowy?\n"
            "6️⃣ Jakie materiały reklamowe masz? _(nagłówki / opisy / link do filmu / obrazy)_"
        )
    elif mode == "pro":
        intro = (
            "🔵 *Tworzymy nową kampanię Google Ads — tryb PRO*\n"
            "Przeprowadzę Cię przez cały proces krok po kroku.\n"
            "Napisz `anuluj` żeby przerwać w dowolnym momencie.\n\n"
            "Zaczynamy. Odpowiedz proszę na te 5 pytań:\n\n"
            "1️⃣ Jaki jest główny cel kampanii? _(sprzedaż / leady / telefony / ruch / świadomość / wizyty w sklepie / instalacje apki)_\n"
            "2️⃣ Co dokładnie reklamujemy? _(produkt / usługa / oferta)_\n"
            "3️⃣ Na jaki rynek kierujemy? _(kraj / miasta / regiony)_\n"
            "4️⃣ Jaki masz budżet dzienny lub miesięczny?\n"
            "5️⃣ Jaki typ kampanii chcesz? _(Search / Performance Max / Display / Video / Demand Gen / Shopping / App — lub 'nie wiem')_"
        )
    else:  # auto
        intro = (
            "🔵 *Tworzymy kampanię Google Ads!*\n"
            "Napisz `anuluj` żeby przerwać w dowolnym momencie.\n\n"
            "Powiedz mi co chcesz zrobić — dopasuję proces do potrzeb.\n"
            "Możesz np. podać od razu typ kampanii, budżet, link i materiały,\n"
            "albo opisać cel i pomogę dobrać najlepsze rozwiązanie."
        )

    try:
        resp = app.client.chat_postMessage(channel=source_channel, text=intro)
        thread_ts = resp["ts"]
    except Exception as e:
        logger.error("/kampaniagoogle post error: %s", e)
        return

    messages = [{"role": "assistant", "content": intro}]
    if extra_context:
        messages.append({"role": "user", "content": extra_context})

    _ctx.google_campaign_wizard[user_id] = {
        "messages": messages,
        "source_channel": source_channel,
        "thread_ts": thread_ts,
        "mode": mode,              # "auto", "simple", "pro"
        "resolved_mode": mode if mode != "auto" else "",  # filled after auto-detection
    }

    # Track thread so follow-ups work even if wizard state is lost
    _ctx.bot_threads.add((source_channel, thread_ts))
    # Clear old conversation history — prevent data leaking from previous campaigns
    _ctx.conversation_history.pop(user_id, None)

    # If extra context was provided, immediately process through Claude
    if extra_context:
        def _init_say(text):
            _google_wizard_post(user_id, text)
        _handle_google_campaign_wizard(user_id, None, [], _init_say)

    logger.info("/kampaniagoogle (%s) started by %s in %s", mode, user_id, source_channel)


logger.info("✅ /kampaniagoogle handler zarejestrowany")


@app.action(re.compile(r"^gw_btn_"))
def handle_gw_button(ack, body, action):
    """Handle wizard interactive button clicks.
    Injects the selected value into the wizard as if the user typed it.
    """
    ack()
    try:
        user_id = body["user"]["id"]
        selected_value = action.get("value", "")
        channel_id = body["container"]["channel_id"]
        message_ts = body["container"]["message_ts"]
        thread_ts = body["container"].get("thread_ts") or message_ts

        # Update the original message: replace action blocks with a "confirmed" note
        original_blocks = body.get("message", {}).get("blocks", [])
        new_blocks = []
        for blk in original_blocks:
            if blk.get("type") == "actions":
                # Replace button row with a text confirmation
                new_blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"✅ *{selected_value}*"},
                })
            elif blk.get("type") == "context" and blk.get("elements", [{}])[0].get("text", "").startswith("_Kliknij"):
                pass  # Remove the hint line
            else:
                new_blocks.append(blk)

        try:
            app.client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=f"✅ {selected_value}",
                blocks=new_blocks,
            )
        except Exception as upd_err:
            logger.warning("gw_button chat_update failed: %s", upd_err)

        # Feed answer into wizard
        if user_id not in _ctx.google_campaign_wizard:
            logger.warning("gw_button: no wizard for user %s", user_id)
            return

        def _say(text):
            _google_wizard_post(user_id, text)

        _handle_google_campaign_wizard(user_id, selected_value, [], _say)

    except Exception as e:
        logger.error("handle_gw_button error: %s", e, exc_info=True)


def _handle_google_campaign_wizard(user_id: str, user_message: str | None, files: list = None, say_fn=None) -> bool:
    """
    Handle a channel thread reply for a user in the /kampaniagoogle wizard.
    Uses Claude to drive the conversation dynamically.
    Supports AUTO/SIMPLE/PRO modes with automatic resolution and switching.
    user_message=None means the message was already appended (e.g. from slash command extra context).
    Returns True if message was consumed by the wizard, False otherwise.
    """
    if files is None:
        files = []
    # Support old call signature: (user_id, msg, say_fn) without files
    if callable(files):
        say_fn = files
        files = []
    if user_id not in _ctx.google_campaign_wizard:
        return False

    wizard = _ctx.google_campaign_wizard[user_id]
    current_mode = wizard.get("resolved_mode") or wizard.get("mode", "auto")

    if user_message is not None:
        msg_lower = user_message.strip().lower()

        # Anulowanie
        if msg_lower in ("anuluj", "cancel", "stop", "przerwij"):
            del _ctx.google_campaign_wizard[user_id]
            say_fn("❌ Tworzenie kampanii Google Ads anulowane.")
            return True

        # Jawne przełączenie SIMPLE → PRO
        if msg_lower in ("pro", "full", "szczegolowo", "szczegółowo") and current_mode == "simple":
            wizard["resolved_mode"] = "pro"
            wizard["messages"].append({"role": "user", "content": "Chcę przejść w pełny tryb PRO."})
            wizard["messages"].append({"role": "assistant", "content": (
                "🔵 OK — przechodzimy w tryb PRO. Teraz zrobię pełny setup kampanii.\n"
                "Kontynuuję z danymi które już mam."
            )})
            say_fn("🔵 OK — przechodzimy w tryb PRO. Teraz zrobię pełny setup kampanii.\nKontynuuję z danymi które już mam.")
            return True

        # Build message content with file info
        content = user_message
        if files:
            file_names = [f.get("name", "plik") for f in files]
            content += f"\n[Załączone pliki: {', '.join(file_names)}]"
            _file_ids = [f["id"] for f in files]
            downloaded = download_slack_files(_file_ids) if _file_ids else []
            wizard.setdefault("files", []).extend(downloaded)

        wizard["messages"].append({"role": "user", "content": content})

    # --- Wybierz system prompt na podstawie trybu ---
    if current_mode == "auto":
        # Pierwszy raz — Claude analizuje intencję i wybiera tryb
        system_prompt = GOOGLE_CAMPAIGN_AUTO_PROMPT
    elif current_mode == "simple":
        system_prompt = GOOGLE_CAMPAIGN_SIMPLE_PROMPT
    else:  # pro
        system_prompt = GOOGLE_CAMPAIGN_SYSTEM_PROMPT

    try:
        response = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system=system_prompt,
            messages=wizard["messages"],
        )
        assistant_text = response.content[0].text

        # --- AUTO MODE: resolve mode from Claude's response ---
        if current_mode == "auto":
            if "===MODE:SIMPLE===" in assistant_text:
                wizard["resolved_mode"] = "simple"
                assistant_text = assistant_text.replace("===MODE:SIMPLE===", "").strip()
                logger.info("Google wizard AUTO → SIMPLE for user %s", user_id)
            elif "===MODE:PRO===" in assistant_text:
                wizard["resolved_mode"] = "pro"
                assistant_text = assistant_text.replace("===MODE:PRO===", "").strip()
                logger.info("Google wizard AUTO → PRO for user %s", user_id)
            else:
                # Fallback: jeśli Claude nie dał markera, zakładamy PRO
                wizard["resolved_mode"] = "pro"
                logger.warning("Google wizard AUTO: no mode marker, defaulting to PRO for user %s", user_id)

        # --- Detect SIMPLE→PRO switch suggested by Claude ---
        if "===SWITCH:PRO===" in assistant_text:
            wizard["resolved_mode"] = "pro"
            assistant_text = assistant_text.replace("===SWITCH:PRO===", "").strip()
            logger.info("Google wizard SIMPLE → PRO switch for user %s", user_id)

        # Zapisz odpowiedź Claude do historii (bez markerów)
        wizard["messages"].append({"role": "assistant", "content": assistant_text})

        # Trim historii jeśli za długa (zachowaj ostatnie 30 wiadomości)
        if len(wizard["messages"]) > 30:
            wizard["messages"] = wizard["messages"][-30:]

        # Sprawdź czy kampania jest gotowa
        if "===KAMPANIA_GOOGLE_GOTOWA===" in assistant_text:
            clean_text = assistant_text.replace("===KAMPANIA_GOOGLE_GOTOWA===", "").strip()
            say_fn(clean_text)

            # Parse JSON from Claude's response
            _gjson = None
            for _pattern in (
                r"```json\s*(\{[\s\S]*?\})\s*```",
                r"```\s*(\{[\s\S]*?\})\s*```",
                r"(\{[\s\S]*\"campaign_name\"[\s\S]*\})",
            ):
                _m = re.search(_pattern, assistant_text)
                if _m:
                    try:
                        _gjson = json.loads(_m.group(1))
                        break
                    except json.JSONDecodeError:
                        continue

            if _gjson:
                try:
                    # Detect client → get customer_id
                    accounts_json = os.environ.get("GOOGLE_ADS_CUSTOMER_IDS", "{}")
                    try:
                        accounts_map = json.loads(accounts_json)
                    except json.JSONDecodeError:
                        accounts_map = {}

                    _client_key = _detect_google_client(_gjson, accounts_map)
                    _customer_id = accounts_map.get(_client_key, "") if _client_key else ""

                    if not _customer_id:
                        say_fn(
                            "⚠️ Nie znalazłem konta Google Ads dla tego klienta.\n"
                            "Sprawdź konfigurację `GOOGLE_ADS_CUSTOMER_IDS` na Render.\n"
                            "Możesz utworzyć kampanię ręcznie korzystając z JSON powyżej."
                        )
                        del _ctx.google_campaign_wizard[user_id]
                        _ctx.save_wizard_state()
                        return True

                    say_fn(f"📋 Tworzę szkic kampanii w Google Ads (konto: `{_client_key}`)...")
                    _draft = create_google_campaign_draft(_gjson, _customer_id)

                    if "error" in _draft:
                        say_fn(f"❌ Błąd tworzenia kampanii: {_draft['error']}")
                    else:
                        _preview = generate_google_campaign_preview(_gjson, _draft)
                        say_fn(_preview)
                        say_fn(
                            "✅ Kampania jest w Google Ads — *wstrzymana*. "
                            "Włącz ją ręcznie w panelu gdy będziesz gotowy.\n"
                            f"🔗 https://ads.google.com/aw/campaigns?campaignId={_draft['campaign_id']}"
                        )

                except Exception as _de:
                    logger.error("Google wizard draft creation error: %s", _de, exc_info=True)
                    say_fn(f"❌ Błąd tworzenia draftu: {_de}")
            else:
                logger.warning("Google wizard: no JSON found in completion response")
                say_fn("⚠️ Nie znalazłem JSON w odpowiedzi. Możesz skopiować dane ręcznie.")

            del _ctx.google_campaign_wizard[user_id]
            _ctx.save_wizard_state()
        else:
            say_fn(assistant_text)

    except Exception as e:
        logger.error("Google campaign wizard Claude error: %s", e)
        say_fn(f"❌ Błąd komunikacji z AI: {e}")

    return True


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
# STANDUP wyłączony — nikt nie robi
# scheduler.add_job(send_standup_questions,    'cron', day_of_week='mon-fri', hour=9, minute=0,  id='standup_send')
# scheduler.add_job(post_standup_summary,      'cron', day_of_week='mon-fri', hour=9, minute=30, id='standup_summary')
scheduler.add_job(weekly_industry_news,      'cron', day_of_week='mon',     hour=9, minute=0,  id='industry_news')
# reminders job removed — Slack chat.scheduleMessage handles delivery natively
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

# ── Meta Ads history backfill (runs once in background on startup) ─────────────
from config.constants import AD_CLIENTS
def _run_meta_backfill():
    for _client in AD_CLIENTS:
        try:
            backfill_campaign_history(_client, days_back=90)
        except Exception as _e:
            logger.warning("Meta backfill error (%s): %s", _client, _e)
threading.Thread(target=_run_meta_backfill, daemon=True).start()

# ── start ─────────────────────────────────────────────────────────────────────

# Register Slack error alerts (ERRORS_CHANNEL_ID or GENERAL_CHANNEL_ID env var)
_err_channel = os.environ.get("ERRORS_CHANNEL_ID") or os.environ.get("GENERAL_CHANNEL_ID", "")
if _err_channel:
    logging.getLogger().addHandler(_SlackErrorHandler(_err_channel))
    logger.info("SlackErrorHandler registered → channel %s", _err_channel)

handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
print("⚡️ Bot działa!")

# ── HTTP status server (aiohttp, port STATUS_PORT/8080) ───────────────────────
from scripts.status_server import start_status_server_thread
start_status_server_thread()

handler.start()
