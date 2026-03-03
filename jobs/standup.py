"""Daily standup — wysyłka pytań, zbieranie odpowiedzi, podsumowanie."""
import json
import logging
import pytz
from datetime import datetime

import _ctx
from config.constants import TEAM_MEMBERS, STANDUP_FILE, STANDUP_CHANNEL, STANDUP_QUESTION

logger = logging.getLogger(__name__)


def _load_standup():
    import os
    try:
        if os.path.exists(STANDUP_FILE):
            with open(STANDUP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_standup(data):
    import os
    try:
        os.makedirs(os.path.dirname(STANDUP_FILE), exist_ok=True)
        with open(STANDUP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"_save_standup error: {e}")


def _today_standup_key():
    return datetime.now(pytz.timezone("Europe/Warsaw")).strftime("%Y-%m-%d")


def send_standup_questions():
    """9:00 pn-pt — DM do wszystkich członków teamu z pytaniem standupowym."""
    today = _today_standup_key()
    data  = _load_standup()

    if today in data and data[today].get("sent"):
        logger.info(f"Standup {today} już wysłany, pomijam.")
        return

    session = {
        "date":           today,
        "sent_at":        datetime.now().isoformat(),
        "sent":           True,
        "responses":      {},
        "summary_posted": False,
    }

    sent_count = 0
    for member in TEAM_MEMBERS:
        uid = member["slack_id"]
        try:
            _ctx.app.client.chat_postMessage(channel=uid, text=STANDUP_QUESTION)
            sent_count += 1
            logger.info(f"📤 Standup DM → {member['name']} ({uid})")
        except Exception as e:
            logger.error(f"Standup DM error ({member['name']}): {e}")

    data[today] = session
    _save_standup(data)
    logger.info(f"✅ Standup {today} wysłany do {sent_count}/{len(TEAM_MEMBERS)} osób")


def handle_standup_dm(user_id, user_name, text):
    """Łapie odpowiedź na standup w oknie 9:00–9:45.
    Zwraca True jeśli wiadomość była odpowiedzią standupową."""
    today = _today_standup_key()
    data  = _load_standup()

    if today not in data or not data[today].get("sent"):
        return False
    if data[today].get("summary_posted"):
        return False

    now_w  = datetime.now(pytz.timezone("Europe/Warsaw"))
    cutoff = now_w.replace(hour=9, minute=45, second=0, microsecond=0)
    if now_w > cutoff:
        return False

    data[today]["responses"][user_id] = {
        "text":       text,
        "replied_at": datetime.now().isoformat(),
        "name":       user_name,
    }
    _save_standup(data)
    logger.info(f"📥 Standup odpowiedź: {user_name}")
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
    lines = [f"📋 *Standup — {date_str}*\n"]

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
    """9:30 pn-pt — postuje podsumowanie standupu do kanału."""
    today = _today_standup_key()
    data  = _load_standup()

    if today not in data or not data[today].get("sent"):
        logger.info("Standup nie był wysłany dziś, pomijam summary.")
        return
    if data[today].get("summary_posted"):
        logger.info(f"Standup {today} summary już wysłany.")
        return

    channel = STANDUP_CHANNEL
    if not channel:
        logger.error("STANDUP_CHANNEL nie ustawiony — brak kanału do postu.")
        return

    summary = _build_standup_summary(data[today])
    try:
        _ctx.app.client.chat_postMessage(channel=channel, text=summary)
        data[today]["summary_posted"] = True
        _save_standup(data)
        logger.info(f"✅ Standup summary {today} wysłany na {channel}")
    except Exception as e:
        logger.error(f"Błąd post standup summary: {e}")


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
        respond(f"📤 Standup wysłany do {len(TEAM_MEMBERS)} osób!")
        return

    if text == "summary":
        if not session or not session.get("sent"):
            respond("⚠️ Standup nie był jeszcze wysłany dziś. Użyj `/standup send`.")
            return
        post_standup_summary()
        respond("✅ Summary wysłane!")
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
