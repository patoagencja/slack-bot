"""Daily standup — wysyłka pytań w kanale, zbieranie odpowiedzi z wątku, podsumowanie."""
import json
import logging
import os
import pytz
from datetime import datetime, timedelta

import _ctx
from config.constants import TEAM_MEMBERS, STANDUP_FILE

_ZARZOND_CHANNEL_NAME = "zarzondpato"
_zarzond_channel_id_cache = None

def _get_zarzond_channel_id():
    global _zarzond_channel_id_cache
    if _zarzond_channel_id_cache:
        return _zarzond_channel_id_cache
    try:
        result = _ctx.app.client.conversations_list(types="public_channel,private_channel", limit=200)
        for ch in result.get("channels", []):
            if ch.get("name") == _ZARZOND_CHANNEL_NAME:
                _zarzond_channel_id_cache = ch["id"]
                return _zarzond_channel_id_cache
    except Exception as e:
        logger.warning(f"Nie znaleziono kanału #{_ZARZOND_CHANNEL_NAME}: {e}")
    return None

logger = logging.getLogger(__name__)

_STANDUP_MARKER = "Szybki standup"  # unikalny tekst do wyszukiwania w historii kanału


# ── persistence ────────────────────────────────────────────────────────────────

def _load_standup():
    try:
        if os.path.exists(STANDUP_FILE):
            with open(STANDUP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_standup(data):
    try:
        os.makedirs(os.path.dirname(STANDUP_FILE), exist_ok=True)
        with open(STANDUP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"_save_standup error: {e}")


def _today_standup_key():
    return datetime.now(pytz.timezone("Europe/Warsaw")).strftime("%Y-%m-%d")


def _get_standup_channel():
    """Kanał do standupu — DRE channel."""
    return os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")


# ── recovery helpers (Render kasuje pliki przy restarcie) ──────────────────────

def _find_thread_ts_in_history(channel: str, today: str):
    """
    Szuka wiadomości standupu w historii kanału.
    Fallback gdy standup.json skasowany przez Render przy deployu.
    """
    try:
        tz = pytz.timezone("Europe/Warsaw")
        today_dt = tz.localize(datetime.strptime(today, "%Y-%m-%d"))
        oldest   = str(today_dt.timestamp())
        latest   = str((today_dt + timedelta(days=1)).timestamp())
        history  = _ctx.app.client.conversations_history(
            channel=channel, oldest=oldest, latest=latest, limit=50
        )
        for msg in history.get("messages", []):
            if msg.get("bot_id") and _STANDUP_MARKER in msg.get("text", ""):
                logger.info(f"Znaleziono standup thread_ts w historii kanału: {msg['ts']}")
                return msg["ts"]
    except Exception as e:
        logger.warning(f"_find_thread_ts_in_history error: {e}")
    return None


def get_today_standup_thread_ts():
    """
    Zwraca (channel_id, thread_ts) dla dzisiejszego standupu.
    Sprawdza najpierw plik, potem historię kanału.
    """
    today   = _today_standup_key()
    data    = _load_standup()
    session = data.get(today, {})

    channel   = session.get("channel") or _get_standup_channel()
    thread_ts = session.get("thread_ts")

    if not thread_ts:
        thread_ts = _find_thread_ts_in_history(channel, today)
        if thread_ts:
            if today in data:
                data[today]["thread_ts"] = thread_ts
                data[today]["channel"]   = channel
                _save_standup(data)

    return channel, thread_ts


def _read_responses_from_thread(channel: str, thread_ts: str) -> dict:
    """
    Odczytuje odpowiedzi z wątku standupu bezpośrednio przez Slack API.
    Używane gdy bot zrestartował się i stracił dane z pamięci / pliku.
    """
    responses = {}
    try:
        replies    = _ctx.app.client.conversations_replies(
            channel=channel, ts=thread_ts, limit=50
        )
        team_by_id = {m["slack_id"]: m for m in TEAM_MEMBERS}
        for msg in replies.get("messages", []):
            if msg.get("ts") == thread_ts:
                continue  # pomijamy wiadomość-rodzica
            uid = msg.get("user", "")
            if uid in team_by_id and uid not in responses:
                responses[uid] = {
                    "text":       msg.get("text", ""),
                    "replied_at": datetime.fromtimestamp(float(msg["ts"])).isoformat(),
                    "name":       team_by_id[uid]["name"],
                }
    except Exception as e:
        logger.warning(f"_read_responses_from_thread error: {e}")
    return responses


# ── main flow ──────────────────────────────────────────────────────────────────

def send_standup_questions():
    """9:00 pn-pt — wysyła pytanie standupowe prywatnie (DM) do każdego członka zespołu."""
    today = _today_standup_key()
    data  = _load_standup()

    if today in data and data[today].get("sent"):
        logger.info(f"Standup {today} już wysłany, pomijam.")
        return

    dm_channels = {}
    for member in TEAM_MEMBERS:
        try:
            res = _ctx.app.client.conversations_open(users=member["slack_id"])
            dm_ch = res["channel"]["id"]
            _ctx.app.client.chat_postMessage(
                channel=dm_ch,
                text=(
                    f"☀️ *Szybki standup* — {today}\n\n"
                    f"1️⃣ Co dziś planujesz robić?\n"
                    f"2️⃣ Jakieś blokery lub czego potrzebujesz od innych?\n\n"
                    f"_Odpowiedz tutaj, skleję o 9:30_ 🙏"
                ),
            )
            dm_channels[member["slack_id"]] = dm_ch
            logger.info(f"✅ Standup DM wysłany do {member['name']} ({dm_ch})")
        except Exception as e:
            logger.error(f"Standup DM error dla {member['name']}: {e}")

    data[today] = {
        "date":        today,
        "sent_at":     datetime.now().isoformat(),
        "sent":        True,
        "dm_channels": dm_channels,
        "responses":   {},
        "summary_posted": False,
    }
    _save_standup(data)


def handle_standup_reply(user_id: str, user_name: str, text: str,
                         msg_thread_ts: str = None, msg_channel: str = None) -> bool:
    """
    Łapie odpowiedź na standup — z DM (okno 9:00–9:45).
    Zwraca True jeśli wiadomość była odpowiedzią standupową.
    """
    today   = _today_standup_key()
    data    = _load_standup()
    session = data.get(today, {})

    if not session.get("sent"):
        return False
    if session.get("summary_posted"):
        return False

    # Okno czasowe 9:00–9:45
    now_w  = datetime.now(pytz.timezone("Europe/Warsaw"))
    cutoff = now_w.replace(hour=9, minute=45, second=0, microsecond=0)
    if now_w > cutoff:
        return False

    # Sprawdź czy wiadomość pochodzi z DM standupu tego użytkownika
    dm_channels = session.get("dm_channels", {})
    if msg_channel and dm_channels.get(user_id) != msg_channel:
        return False
    if not msg_channel and not dm_channels:
        # fallback: stary tryb kanałowy — sprawdź thread_ts
        stored_thread_ts = session.get("thread_ts")
        if stored_thread_ts and msg_thread_ts != stored_thread_ts:
            return False

    data[today]["responses"][user_id] = {
        "text":       text,
        "replied_at": datetime.now().isoformat(),
        "name":       user_name,
    }
    _save_standup(data)
    logger.info(f"📥 Standup odpowiedź z kanału: {user_name}")
    return True


def _build_standup_summary(session):
    """Buduje wiadomość podsumowania standupu."""
    dt       = datetime.fromisoformat(session["sent_at"])
    weekdays = ["poniedziałek","wtorek","środa","czwartek","piątek","sobota","niedziela"]
    months   = ["","stycznia","lutego","marca","kwietnia","maja","czerwca",
                "lipca","sierpnia","września","października","listopada","grudnia"]
    day_name = weekdays[dt.weekday()]
    date_str = f"{day_name} {dt.day} {months[dt.month]}"

    responses = session.get("responses", {})
    lines     = [f"📋 *Standup — {date_str}*\n"]

    answered, no_answer = [], []
    for member in TEAM_MEMBERS:
        uid = member["slack_id"]
        if uid in responses:
            answered.append((member, responses[uid]["text"]))
        else:
            no_answer.append(member)

    for member, answer in answered:
        lines.append(f"✅ *{member['name']}* _({member['role']})_")
        for line in answer.strip().splitlines():
            lines.append(f"   {line}")
        lines.append("")

    if no_answer:
        names = ", ".join(f"*{m['name']}*" for m in no_answer)
        lines.append(f"⏰ Brak odpowiedzi: {names}")

    lines.append(f"\n_{len(answered)}/{len(TEAM_MEMBERS)} osób odpowiedziało_")
    return "\n".join(lines)


def post_standup_summary():
    """9:30 pn-pt — wysyła każdemu plan działania DM + summary zarządowe do #zarzadpato."""
    today = _today_standup_key()
    data  = _load_standup()

    session = data.get(today, {})
    if not session.get("sent"):
        logger.info("Standup nie był wysłany dziś, pomijam summary.")
        return
    if session.get("summary_posted"):
        logger.info(f"Standup {today} summary już wysłany.")
        return

    responses  = session.get("responses", {})
    dm_channels = session.get("dm_channels", {})

    # 1. Każdemu członkowi → spersonalizowany plan działania na dziś (DM)
    for member in TEAM_MEMBERS:
        uid      = member["slack_id"]
        dm_ch    = dm_channels.get(uid)
        response = responses.get(uid, {}).get("text", "")
        if not dm_ch:
            continue
        if response:
            plan_text = (
                f"*Twój plan na dziś — {today}*\n\n"
                f"{response}\n\n"
                f"_Powodzenia! Jeśli coś się zmieni, daj znać w wątku._"
            )
        else:
            plan_text = f"_Nie odpowiedziałeś/aś na standup — jeśli masz pytania, pisz tutaj._"
        try:
            _ctx.app.client.chat_postMessage(channel=dm_ch, text=plan_text)
            logger.info(f"✅ Plan DM wysłany do {member['name']}")
        except Exception as e:
            logger.error(f"Plan DM error dla {member['name']}: {e}")

    # 2. Summary zarządowe → #zarzondpato
    ZARZAD_CHANNEL_ID = _get_zarzond_channel_id()
    if ZARZAD_CHANNEL_ID:
        dt       = datetime.fromisoformat(session["sent_at"])
        weekdays = ["poniedziałek","wtorek","środa","czwartek","piątek","sobota","niedziela"]
        months   = ["","stycznia","lutego","marca","kwietnia","maja","czerwca",
                    "lipca","sierpnia","września","października","listopada","grudnia"]
        date_str = f"{weekdays[dt.weekday()]} {dt.day} {months[dt.month]}"

        lines = [f"*Standup — {date_str}*\n"]
        no_answer = []
        for member in TEAM_MEMBERS:
            uid  = member["slack_id"]
            resp = responses.get(uid, {}).get("text", "")
            if resp:
                lines.append(f"*{member['name']}* _({member['role']})_")
                for line in resp.strip().splitlines():
                    lines.append(f"   {line}")
                lines.append("")
            else:
                no_answer.append(member["name"])

        if no_answer:
            lines.append(f"Brak odpowiedzi: {', '.join(no_answer)}")
        lines.append(f"\n_{len(responses)}/{len(TEAM_MEMBERS)} osób odpowiedziało_")

        try:
            _ctx.app.client.chat_postMessage(
                channel=ZARZAD_CHANNEL_ID,
                text="\n".join(lines),
            )
            logger.info(f"✅ Standup summary zarządowy wysłany do #zarzadpato")
        except Exception as e:
            logger.error(f"Błąd wysyłki do #zarzadpato: {e}")
    else:
        logger.warning(f"Kanał #{_ZARZOND_CHANNEL_NAME} nie znaleziony — pomijam summary zarządowe")

    data[today]["summary_posted"] = True
    _save_standup(data)


# ── slash command ──────────────────────────────────────────────────────────────

def handle_standup_slash(ack, respond, command):
    """Handler dla /standup slash command (rejestrowany w bot.py)."""
    ack()
    text    = (command.get("text") or "").strip().lower()
    today   = _today_standup_key()
    data    = _load_standup()
    session = data.get(today)

    if text == "send":
        if session and session.get("sent"):
            respond("⚠️ Standup na dziś już był wysłany.")
            return
        send_standup_questions()
        respond(f"📤 Standup wysłany do kanału!")
        return

    if text == "summary":
        if not session or not session.get("sent"):
            respond("⚠️ Standup nie był jeszcze wysłany dziś. Użyj `/standup send`.")
            return
        post_standup_summary()
        respond("✅ Summary wysłane w wątku!")
        return

    if not session or not session.get("sent"):
        respond(
            f"📋 *Standup dziś ({today}):* nie wysłany.\n"
            f"Użyj `/standup send` aby wysłać teraz lub poczekaj do 9:00."
        )
        return

    responses = session.get("responses", {})
    answered  = [m for m in TEAM_MEMBERS if m["slack_id"] in responses]
    waiting   = [m for m in TEAM_MEMBERS if m["slack_id"] not in responses]

    msg = f"📋 *Standup {today}* — {len(answered)}/{len(TEAM_MEMBERS)} odpowiedzi\n"
    if answered:
        msg += "✅ " + ", ".join(f"*{m['name']}*" for m in answered) + "\n"
    if waiting:
        msg += "⏰ Czekam na: " + ", ".join(f"*{m['name']}*" for m in waiting)
    if session.get("summary_posted"):
        msg += "\n_Summary już wysłane._"
    respond(msg)
