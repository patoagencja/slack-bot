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
from tools.campaign_creator import (
    download_slack_files, upload_creative_to_meta, parse_campaign_request,
    build_meta_targeting, create_campaign_draft, generate_campaign_preview,
    approve_and_launch_campaign, cancel_campaign_draft, validate_campaign_params,
    get_meta_account_id, generate_campaign_expert_analysis,
)
from tools.voice_transcription import transcribe_slack_audio, SLACK_AUDIO_MIMES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── initialization ────────────────────────────────────────────────────────────
_ctx.app    = App(token=os.environ.get("SLACK_BOT_TOKEN"))
_ctx.claude = Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))

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
    user_message = ' '.join(user_message.split()[1:])  # Usuń wzmianke bota

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
                        app.client.chat_scheduleMessage(
                            channel=_member["slack_id"],
                            text=_cmd["message"],
                            post_at=_ts,
                        )
                        _dm_results.append(f"✅ Zaplanowano do *{_member['name']}* o {_cmd['time']}: _{_cmd['message']}_")
                    except Exception as _e:
                        _dm_results.append(f"❌ Błąd planowania do {_member['name']}: {_e}")
                else:
                    try:
                        app.client.chat_postMessage(
                            channel=_member["slack_id"],
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

    # === PENDING CAMPAIGN: state machine (collecting → expert_review → build) ===
    _CONFIRM_KWS = [
        "zaczynaj", "zaczynamy", "dawaj", "buduj", "budujemy", "robimy",
        "lecimy", "no to lej", "go", "start", "ok buduj", "ok zaczynaj",
        "potwierdź", "potwierdzam", "tak buduj", "tak zaczynaj", "zgoda",
    ]
    if _mention_user_id in _ctx.campaign_pending:
        _pending      = _ctx.campaign_pending[_mention_user_id]
        _pend_state   = _pending.get("state", "collecting")
        _pend_msg_l   = user_message.lower().strip()

        if _pend_state == "expert_review":
            # Sprawdź czy user potwierdza (zaczynaj / ok / etc.) lub podaje zmiany
            _is_confirm = (
                any(kw in _pend_msg_l for kw in _CONFIRM_KWS)
                or (_pend_msg_l in ("ok", "tak", "yes", "ok.", "tak.", "k"))
            )
            if _is_confirm:
                # ── BUDUJ KAMPANIĘ ──────────────────────────────────────────
                _pp     = _pending["params"]
                _pfiles = _pending.get("files", [])
                del _ctx.campaign_pending[_mention_user_id]
                say(text="✅ Zaczynamy! Buduję kampanię...", thread_ts=thread_ts)
                try:
                    _p_account_id = get_meta_account_id(_pp["client_name"])
                    _p_creatives  = []
                    if _pfiles:
                        say(text=f"🎨 Uploaduję {len(_pfiles)} kreacji do Meta...", thread_ts=thread_ts)
                        for _pf_name, _pf_data, _pf_type in _pfiles:
                            try:
                                _p_cr = upload_creative_to_meta(_p_account_id, _pf_data, _pf_type, _pf_name)
                                _p_creatives.append(_p_cr)
                            except Exception as _pce:
                                say(text=f"⚠️ Nie udało się uploadować `{_pf_name}`: {_pce}", thread_ts=thread_ts)
                    _p_targeting = build_meta_targeting(_pp.get("targeting") or {})
                    say(text="📋 Tworzę szkic kampanii w Meta Ads...", thread_ts=thread_ts)
                    _p_draft_ids = create_campaign_draft(_p_account_id, _pp, _p_creatives, _p_targeting)
                    _p_preview   = generate_campaign_preview(
                        _pp, _pp.get("targeting") or {}, len(_p_creatives), _p_draft_ids,
                    )
                    say(text=_p_preview, thread_ts=thread_ts)
                except Exception as _pce:
                    logger.error(f"Campaign build error (mention): {_pce}")
                    say(text=f"❌ Błąd tworzenia kampanii: {str(_pce)}", thread_ts=thread_ts)
            else:
                # ── USER CHCE ZMIANY → zaktualizuj parametry i re-analizuj ──
                _fill = parse_campaign_request(user_message, [])
                _pending["params"] = _merge_pending_campaign_params(
                    _pending["params"], _fill, user_message
                )
                _still_miss = _check_missing_campaign_fields(
                    _pending["params"], _pending.get("files", [])
                )
                if _still_miss:
                    _pending["state"] = "collecting"
                    say(
                        text="❓ Mam zmiany, ale brakuje mi jeszcze:\n\n"
                             + "\n".join(f"• {q}" for q in _still_miss),
                        thread_ts=thread_ts,
                    )
                else:
                    say(text="🔄 Aktualizuję analizę...", thread_ts=thread_ts)
                    _expert_txt = generate_campaign_expert_analysis(
                        _pending["params"], _pending.get("files", [])
                    )
                    if _expert_txt:
                        say(text=_expert_txt, thread_ts=thread_ts)
                    else:
                        say(text="Napisz *zaczynaj* żeby zbudować kampanię.", thread_ts=thread_ts)

        else:  # state == "collecting"
            # Merge odpowiedzi użytkownika z brakującymi polami
            _fill = parse_campaign_request(user_message, [])
            _pending["params"] = _merge_pending_campaign_params(
                _pending["params"], _fill, user_message
            )
            _still_miss = _check_missing_campaign_fields(
                _pending["params"], _pending.get("files", [])
            )
            if _still_miss:
                say(
                    text="❓ Jeszcze brakuje mi:\n\n"
                         + "\n".join(f"• {q}" for q in _still_miss)
                         + "\n\nOdpowiedz na te pytania — zaraz zaczynam! 🚀",
                    thread_ts=thread_ts,
                )
            else:
                # Wszystkie wymagane pola zebrane → ekspert przejmuje
                _pp = _pending["params"]
                _pp["daily_budget"]   = float(_pp.get("daily_budget") or 100)
                _pp["objective"]      = _pp.get("objective") or "OUTCOME_TRAFFIC"
                _pp["call_to_action"] = _pp.get("call_to_action") or "LEARN_MORE"
                _pending["state"]     = "expert_review"
                say(text="🧠 Analizuję kampanię...", thread_ts=thread_ts)
                _expert_txt = generate_campaign_expert_analysis(_pp, _pending.get("files", []))
                if _expert_txt:
                    say(text=_expert_txt, thread_ts=thread_ts)
                else:
                    say(text="Mam wszystko! Napisz *zaczynaj* żeby zbudować kampanię.", thread_ts=thread_ts)
        return

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

    # === CAMPAIGN CREATION: stwórz/zrób kampanię lub upload kreacji ===
    _has_files       = bool(event.get('files'))
    _campaign_create_kws = [
        'stwórz kampanię', 'stworz kampanie', 'zrób kampanię', 'zrob kampanie',
        'nową kampanię', 'nowa kampania', 'utwórz kampanię', 'utworz kampanie',
        'create campaign', 'nowa kampan',
    ]
    if _has_files or any(kw in msg_lower_m for kw in _campaign_create_kws):
        say(text="⏳ Przetwarzam... zaraz wrócę z preview.", thread_ts=thread_ts)
        try:
            # 1. Download files
            _file_ids = [f['id'] for f in event.get('files', [])]
            _cfiles   = download_slack_files(_file_ids) if _file_ids else []

            # 2. Parse request with Claude
            _cparams = parse_campaign_request(user_message, _cfiles)

            # 2b. Sprawdź brakujące wymagane pola
            _missing_qs = _check_missing_campaign_fields(_cparams, _cfiles)
            if _missing_qs:
                # Brakuje danych → zbieramy
                _ctx.campaign_pending[_mention_user_id] = {
                    "params":    _cparams,
                    "files":     _cfiles,
                    "thread_ts": thread_ts,
                    "is_dm":     False,
                    "state":     "collecting",
                }
                say(
                    text=(
                        "❓ *Zanim stworzę kampanię, potrzebuję kilku informacji:*\n\n"
                        + "\n".join(f"• {q}" for q in _missing_qs)
                        + "\n\nOdpowiedz — zaraz analizuję i zaczynam! 🚀"
                    ),
                    thread_ts=thread_ts,
                )
                return

            # Wszystkie wymagane pola są → Apply defaults i przejdź do expert review
            if not _cparams.get("daily_budget"):
                _cparams["daily_budget"] = 100.0
            _cparams["objective"]      = _cparams.get("objective") or "OUTCOME_TRAFFIC"
            _cparams["call_to_action"] = _cparams.get("call_to_action") or "LEARN_MORE"

            # Zapisz do pending → expert_review (użytkownik musi potwierdzić)
            _ctx.campaign_pending[_mention_user_id] = {
                "params":    _cparams,
                "files":     _cfiles,
                "thread_ts": thread_ts,
                "is_dm":     False,
                "state":     "expert_review",
            }

            # Uruchom ekspercką analizę
            say(text="🧠 Analizuję kampanię jako ekspert...", thread_ts=thread_ts)
            _expert_txt = generate_campaign_expert_analysis(_cparams, _cfiles)
            if _expert_txt:
                say(text=_expert_txt, thread_ts=thread_ts)
            else:
                say(
                    text="✅ Mam wszystko! Napisz *zaczynaj* żeby zbudować kampanię.",
                    thread_ts=thread_ts,
                )

        except Exception as _ce:
            logger.error(f"Campaign creation error: {_ce}")
            say(text=f"❌ Błąd: {str(_ce)}", thread_ts=thread_ts)
        return

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

    SYSTEM_PROMPT = f"""
# DATA
Dzisiaj: {today_formatted} ({today_iso}). Pytania o "styczeń 2026" czy wcześniej = PRZESZŁOŚĆ, masz dane!

# KIM JESTEŚ
Sebol — asystent agencji marketingowej Pato. Pomagasz w WSZYSTKIM co dotyczy codziennej pracy agencji: analiza kampanii, organizacja teamu, emaile, raporty, pytania, decyzje. Jesteś częścią teamu — nie jesteś tylko narzędziem do raportów.

# CO POTRAFISZ (lista funkcji gdy ktoś pyta lub się wita)
📊 *Kampanie* — analizujesz Meta Ads i Google Ads w czasie rzeczywistym (CTR, ROAS, spend, konwersje, alerty)
📧 *Emaile* — codzienne podsumowanie ważnych emaili Daniela o 16:00 (+ na żądanie: "test email")
📅 *Team* — pracownicy zgłaszają nieobecności i prośby przez DM, Ty zbierasz i raportujesz Danielowi o 17:00 na #zarzondpato
📋 *Prośby* — zapisujesz prośby teamu (#ID), Daniel zamyka je przez "@Sebol zamknij #N"
🧠 *Daily Digest* — codziennie o 9:00 raport DRE z benchmarkami i smart rekomendacjami
📈 *Weekly Learnings* — co poniedziałek i czwartek o 8:30 analiza wzorców kampanii
⚡ *Alerty budżetowe* — pilnujesz żeby kampanie nie przebijały budżetu
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
- get_google_ads_data() → Google Ads
NIGDY nie mów "nie mam dostępu" - zawsze najpierw użyj narzędzi!

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
    ]

    try:
        user_id = event.get('user')
        history = get_conversation_history(user_id)

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

    # Helper: zawsze odpowiada w głównym kanale DM (Chat tab), bez thread_ts.
    # Slack Bolt's say() może automatycznie dodawać thread_ts z eventu,
    # co powoduje że odpowiedzi trafiają do History zamiast do Chat.
    def _say_dm(text="", **_kw):
        _txt = text or _kw.get("text", "")
        app.client.chat_postMessage(channel=event.get("channel"), text=_txt)

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
    if event.get("subtype") == "bot_message":
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

    # Guard: jeśli wiadomość nadal pusta (sama głosówka bez tekstu i bez transkrypcji) — pomiń
    if not user_message.strip() and _audio_files:
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
        _seba_m = re.search(r'\b(seba|sebol)\b', user_message, re.IGNORECASE)
        if not _seba_m:
            return
        logger.info(f"SEBA TRIGGER → {user_message!r}")
        _clean = re.sub(r'\b(seba|sebol)\b', "", user_message, count=1, flags=re.IGNORECASE).strip()
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
                            app.client.chat_scheduleMessage(
                                channel=_member["slack_id"],
                                text=_cmd["message"],
                                post_at=_ts,
                            )
                            _dm_results.append(f"✅ Zaplanowano do *{_member['name']}* o {_cmd['time']}: _{_cmd['message']}_")
                        except Exception as _e:
                            _dm_results.append(f"❌ Błąd planowania do {_member['name']}: {_e}")
                    else:
                        try:
                            app.client.chat_postMessage(
                                channel=_member["slack_id"],
                                text=_cmd["message"],
                            )
                            _dm_results.append(f"✅ Wysłano do *{_member['name']}*: _{_cmd['message']}_")
                        except Exception as _e:
                            _dm_results.append(f"❌ Błąd wysyłania do {_member['name']}: {_e}")
                if _dm_results:
                    _say_dm("\n".join(_dm_results))
                    return

        # === CAMPAIGN in DM: sprawdź zanim handle_employee_dm połknie request ===
        _dm_text_l = user_message.lower()
        _dm_camp_kws = [
            'stwórz kampanię', 'stworz kampanie', 'zrób kampanię', 'zrob kampanie',
            'nową kampanię', 'nowa kampania', 'utwórz kampanię', 'utworz kampanie',
            'create campaign', 'nowa kampan',
        ]
        _dm_approve_m = re.search(r'(zatwierdź|zatwierdz|uruchom)\s+kampanię\s+(\d+)', _dm_text_l)
        _dm_cancel_m  = re.search(r'(anuluj|usuń|usun|skasuj)\s+kampanię\s+(\d+)', _dm_text_l)
        _dm_has_files = bool(event.get('files'))

        # === PENDING CAMPAIGN (DM): state machine (collecting → expert_review → build) ===
        _DM_CONFIRM_KWS = [
            "zaczynaj", "zaczynamy", "dawaj", "buduj", "budujemy", "robimy",
            "lecimy", "no to lej", "go", "start", "ok buduj", "ok zaczynaj",
            "potwierdź", "potwierdzam", "tak buduj", "tak zaczynaj", "zgoda",
        ]
        if user_id in _ctx.campaign_pending:
            _dm_pending    = _ctx.campaign_pending[user_id]
            _dm_pend_state = _dm_pending.get("state", "collecting")
            _dm_msg_l      = user_message.lower().strip()

            if _dm_pend_state == "expert_review":
                _dm_is_confirm = (
                    any(kw in _dm_msg_l for kw in _DM_CONFIRM_KWS)
                    or (_dm_msg_l in ("ok", "tak", "yes", "ok.", "tak.", "k"))
                )
                if _dm_is_confirm:
                    # ── BUDUJ KAMPANIĘ ──────────────────────────────────────
                    _dm_pp     = _dm_pending["params"]
                    _dm_pfiles = _dm_pending.get("files", [])
                    del _ctx.campaign_pending[user_id]
                    _say_dm(text="✅ Zaczynamy! Buduję kampanię...")
                    try:
                        _dm_p_account_id = get_meta_account_id(_dm_pp["client_name"])
                        _dm_p_creatives  = []
                        if _dm_pfiles:
                            _say_dm(text=f"🎨 Uploaduję {len(_dm_pfiles)} kreacji do Meta...")
                            for _dm_pf_name, _dm_pf_data, _dm_pf_type in _dm_pfiles:
                                try:
                                    _dm_p_cr = upload_creative_to_meta(
                                        _dm_p_account_id, _dm_pf_data, _dm_pf_type, _dm_pf_name
                                    )
                                    _dm_p_creatives.append(_dm_p_cr)
                                except Exception as _dm_pce:
                                    _say_dm(text=f"⚠️ Nie udało się uploadować `{_dm_pf_name}`: {_dm_pce}")
                        _dm_p_targeting = build_meta_targeting(_dm_pp.get("targeting") or {})
                        _say_dm(text="📋 Tworzę szkic kampanii w Meta Ads...")
                        _dm_p_draft_ids = create_campaign_draft(
                            _dm_p_account_id, _dm_pp, _dm_p_creatives, _dm_p_targeting
                        )
                        _dm_p_preview = generate_campaign_preview(
                            _dm_pp, _dm_pp.get("targeting") or {}, len(_dm_p_creatives), _dm_p_draft_ids,
                        )
                        _say_dm(text=_dm_p_preview)
                    except Exception as _dm_pce:
                        logger.error(f"Campaign build error (DM): {_dm_pce}")
                        _say_dm(text=f"❌ Błąd tworzenia kampanii: {str(_dm_pce)}")
                else:
                    # ── USER CHCE ZMIANY → zaktualizuj i re-analizuj ────────
                    _dm_fill = parse_campaign_request(user_message, [])
                    _dm_pending["params"] = _merge_pending_campaign_params(
                        _dm_pending["params"], _dm_fill, user_message
                    )
                    _dm_still_miss = _check_missing_campaign_fields(
                        _dm_pending["params"], _dm_pending.get("files", [])
                    )
                    if _dm_still_miss:
                        _dm_pending["state"] = "collecting"
                        _say_dm(
                            text="❓ Mam zmiany, ale brakuje mi jeszcze:\n\n"
                                 + "\n".join(f"• {q}" for q in _dm_still_miss)
                        )
                    else:
                        _say_dm(text="🔄 Aktualizuję analizę...")
                        _dm_expert_txt = generate_campaign_expert_analysis(
                            _dm_pending["params"], _dm_pending.get("files", [])
                        )
                        if _dm_expert_txt:
                            _say_dm(text=_dm_expert_txt)
                        else:
                            _say_dm(text="Napisz *zaczynaj* żeby zbudować kampanię.")

            else:  # state == "collecting"
                _dm_fill = parse_campaign_request(user_message, [])
                _dm_pending["params"] = _merge_pending_campaign_params(
                    _dm_pending["params"], _dm_fill, user_message
                )
                _dm_still_miss = _check_missing_campaign_fields(
                    _dm_pending["params"], _dm_pending.get("files", [])
                )
                if _dm_still_miss:
                    _say_dm(
                        text="❓ Jeszcze brakuje mi:\n\n"
                             + "\n".join(f"• {q}" for q in _dm_still_miss)
                             + "\n\nOdpowiedz — zaraz analizuję i zaczynam! 🚀"
                    )
                else:
                    _dm_pp = _dm_pending["params"]
                    _dm_pp["daily_budget"]   = float(_dm_pp.get("daily_budget") or 100)
                    _dm_pp["objective"]      = _dm_pp.get("objective") or "OUTCOME_TRAFFIC"
                    _dm_pp["call_to_action"] = _dm_pp.get("call_to_action") or "LEARN_MORE"
                    _dm_pending["state"]     = "expert_review"
                    _say_dm(text="🧠 Analizuję kampanię jako ekspert...")
                    _dm_expert_txt = generate_campaign_expert_analysis(_dm_pp, _dm_pending.get("files", []))
                    if _dm_expert_txt:
                        _say_dm(text=_dm_expert_txt)
                    else:
                        _say_dm(text="Mam wszystko! Napisz *zaczynaj* żeby zbudować kampanię.")
            return

        if _dm_approve_m:
            _camp_id = _dm_approve_m.group(2)
            _say_dm(text=f"🚀 Uruchamiam kampanię `{_camp_id}`...")
            _say_dm(text=approve_and_launch_campaign(_camp_id))
            return

        if _dm_cancel_m:
            _camp_id = _dm_cancel_m.group(2)
            _say_dm(text=cancel_campaign_draft(_camp_id))
            return

        if _dm_has_files or any(kw in _dm_text_l for kw in _dm_camp_kws):
            _say_dm(text="⏳ Przetwarzam... zaraz wrócę z preview.")
            try:
                _file_ids = [f['id'] for f in event.get('files', [])]
                _cfiles   = download_slack_files(_file_ids) if _file_ids else []
                _cparams  = parse_campaign_request(user_message, _cfiles)

                # Sprawdź brakujące wymagane pola
                _dm_missing_qs = _check_missing_campaign_fields(_cparams, _cfiles)
                if _dm_missing_qs:
                    _ctx.campaign_pending[user_id] = {
                        "params": _cparams,
                        "files":  _cfiles,
                        "is_dm":  True,
                        "state":  "collecting",
                    }
                    _say_dm(
                        text=(
                            "❓ *Zanim stworzę kampanię, potrzebuję kilku informacji:*\n\n"
                            + "\n".join(f"• {q}" for q in _dm_missing_qs)
                            + "\n\nOdpowiedz — zaraz analizuję i zaczynam! 🚀"
                        )
                    )
                    return

                # Wszystkie pola są → Apply defaults i przejdź do expert_review
                if not _cparams.get("daily_budget"):
                    _cparams["daily_budget"] = 100.0
                _cparams["objective"]      = _cparams.get("objective") or "OUTCOME_TRAFFIC"
                _cparams["call_to_action"] = _cparams.get("call_to_action") or "LEARN_MORE"

                _ctx.campaign_pending[user_id] = {
                    "params": _cparams,
                    "files":  _cfiles,
                    "is_dm":  True,
                    "state":  "expert_review",
                }

                # Uruchom ekspercką analizę
                _say_dm(text="🧠 Analizuję kampanię jako ekspert...")
                _dm_init_expert = generate_campaign_expert_analysis(_cparams, _cfiles)
                if _dm_init_expert:
                    _say_dm(text=_dm_init_expert)
                else:
                    _say_dm(text="✅ Mam wszystko! Napisz *zaczynaj* żeby zbudować kampanię.")

            except Exception as _ce:
                logger.error(f"Campaign creation DM error: {_ce}")
                _say_dm(text=f"❌ Błąd: {str(_ce)}")
            return

        # Guard: tylko jeśli message zawiera znane employee keywords
        if any(kw in _dm_text_l for kw in EMPLOYEE_MSG_KEYWORDS):
            if handle_employee_dm(user_id, user_name, user_message, _say_dm):
                return

    # Email summary trigger — wyniki zawsze na DM
    if any(t in text_lower for t in ["test email", "email test", "email summary"]):
        logger.info(f"📧 Email trigger od {user_id}, channel_type={event.get('channel_type')}")
        say("📧 Uruchamiam Email Summary... wyślę Ci to na DM.")
        try:
            email_config = get_user_email_config("UTE1RN6SJ")
            if not email_config:
                say("❌ Brak konfiguracji email (`EMAIL_ACCOUNTS`). Napisz do admina.")
                return
            daily_email_summary_slack()
        except Exception as e:
            say(f"❌ Błąd: `{str(e)}`")
            logger.error(f"Błąd test email trigger: {e}")
        return

    # ── Fetch last 100 messages from Slack DM for conversation context ──────────
    try:
        _hist = app.client.conversations_history(
            channel=event.get("channel"), limit=100,
        )
        _raw = _hist.get("messages", [])[::-1]      # odwróć: najstarsze pierwsze
        _dm_msgs: list[dict] = []
        for _m in _raw:
            if _m.get("ts") == event.get("ts"):
                continue                             # pomiń aktualną wiadomość
            _t = (_m.get("text") or "").strip()
            if not _t:
                continue
            _role = "assistant" if (_m.get("bot_id") or _m.get("subtype") == "bot_message") else "user"
            _dm_msgs.append({"role": _role, "content": _t})
        _dm_msgs.append({"role": "user", "content": user_message})
        # Scal consecutive same-role (Anthropic wymaga naprzemiennych ról)
        _merged: list[dict] = []
        for _m in _dm_msgs:
            if _merged and _merged[-1]["role"] == _m["role"]:
                _merged[-1]["content"] += "\n" + _m["content"]
            else:
                _merged.append(dict(_m))
        while _merged and _merged[0]["role"] != "user":
            _merged.pop(0)
        if not _merged:
            _merged = [{"role": "user", "content": user_message}]
    except Exception:
        _merged = [{"role": "user", "content": user_message}]

    _today_dm = datetime.now()
    _dm_system = (
        f"Dzisiaj: {_today_dm.strftime('%d %B %Y')} ({_today_dm.strftime('%Y-%m-%d')}).\n\n"
        "Jesteś Sebol — asystent agencji marketingowej Pato. Rozmawiasz z pracownikiem przez DM na Slacku.\n"
        "NIE jesteś Claude od Anthropic — jesteś Seblem, botem stworzonym dla agencji Pato.\n"
        "Pomagasz z kampaniami (Meta Ads / Google Ads), emailami, teamem, raportami i codzienną pracą agencji.\n\n"
        "Klienci Meta: 'instax/fuji', 'zbiorcze', 'drzwi dre'. Google: 'dre', 'dre 2024', 'dre 2025', 'm2', 'pato'.\n"
        "Benchmarki Meta: ROAS >3.0, CTR 1.5-2.5%, CPC 3-8 PLN. Google Search: CTR 2-5%, CPC 2-10 PLN.\n\n"
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
            elif _tb.name == "manage_email":
                _inp = {k: v for k, v in _tb.input.items() if v is not None and k != "user_id"}
                _tr  = email_tool(user_id=user_id, **_inp)
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
    except Exception as e:
        logger.error(f"Błąd DM handler: {e}")
        _say_dm(text=f"Przepraszam, wystąpił błąd: {str(e)}")


# ── /standup slash command ────────────────────────────────────────────────────

app.command("/standup")(handle_standup_slash)
logger.info("✅ /standup handler zarejestrowany")


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
scheduler.start()

print(f"✅ Scheduler załadowany! Jobs: {len(scheduler.get_jobs())}")
print("✅ Scheduler wystartował!")

# Odbuduj dane nieobecności z historii Slacka po starcie/deployu
try:
    sync_availability_from_slack()
except Exception as _e:
    print(f"⚠️ sync_availability_from_slack startup error: {_e}")

# ── start ─────────────────────────────────────────────────────────────────────

handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
print("⚡️ Bot działa!")
handler.start()
