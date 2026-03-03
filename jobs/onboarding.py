"""Client onboarding checklist system."""
import re
import json
import logging
from datetime import datetime

import _ctx
from config.constants import ONBOARDING_FILE, ONBOARDING_CHECKLIST

logger = logging.getLogger(__name__)


def _load_onboardings():
    import os
    try:
        if os.path.exists(ONBOARDING_FILE):
            with open(ONBOARDING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_onboardings(data):
    import os
    try:
        os.makedirs(os.path.dirname(ONBOARDING_FILE), exist_ok=True)
        with open(ONBOARDING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"_save_onboardings error: {e}")


def _onboarding_key(client_name):
    return client_name.lower().replace(" ", "_")


def _render_onboarding_message(ob):
    """Buduje wiadomość Slack z aktualnym stanem checklisty."""
    items      = ob["items"]
    done_count = sum(1 for i in items if i["done"])
    total      = len(items)
    pct        = int(done_count / total * 100)

    bar_filled   = int(pct / 10)
    progress_bar = "█" * bar_filled + "░" * (10 - bar_filled)

    lines = [f"🚀 *Onboarding: {ob['client_name']}*",
             f"Postęp: [{progress_bar}] *{done_count}/{total}* ({pct}%)\n"]

    for item in items:
        check     = "✅" if item["done"] else "⬜"
        done_info = ""
        if item["done"] and item.get("done_by"):
            done_info = f" _{item['done_by']}_"
        lines.append(f"{check} *{item['id']}.* {item['emoji']} {item['name']}{done_info}")

    if done_count == total:
        lines.append("\n🎉 *Onboarding zakończony! Klient gotowy do działania.* 🎉")
    else:
        remaining = [str(i["id"]) for i in items if not i["done"]]
        lines.append(f"\n_Aby oznaczyć jako gotowe, odpowiedz w tym wątku: `@Sebol done {remaining[0]}` lub np. `@Sebol done 1 2 3`_")

    return "\n".join(lines)


def _find_onboarding_by_thread(thread_ts, channel_id, current_ts=None):
    """Zwraca (key, ob) po thread_ts + channel_id.
    Jeśli brak w pliku (np. po restarcie), odtwarza z historii wątku."""
    data = _load_onboardings()
    for key, ob in data.items():
        if ob.get("message_ts") == thread_ts and ob.get("channel_id") == channel_id:
            return key, ob
    return _recover_onboarding_from_thread(channel_id, thread_ts, exclude_ts=current_ts)


def handle_onboard_slash(ack, respond, command):
    """Handler dla /onboard slash command (rejestrowany w bot.py)."""
    ack()
    text       = (command.get("text") or "").strip()
    channel_id = command.get("channel_id", "")
    user_id    = command.get("user_id", "")

    if not text:
        respond("Użycie: `/onboard [nazwa klienta]`\nPrzykład: `/onboard DRE`")
        return

    client_name = text.strip()
    key = _onboarding_key(client_name)

    data = _load_onboardings()
    if key in data and not data[key].get("completed"):
        respond(
            f"⚠️ Onboarding *{client_name}* już istnieje i jest w toku.\n"
            f"Idź do wątku: przeskocz do <#{data[key]['channel_id']}>"
        )
        return

    try:
        ui        = _ctx.app.client.users_info(user=user_id)
        initiator = (ui["user"].get("real_name")
                     or ui["user"].get("profile", {}).get("display_name")
                     or "ktoś")
    except Exception:
        initiator = "ktoś"

    ob = {
        "client_name": client_name,
        "created_at":  datetime.now().isoformat(),
        "created_by":  initiator,
        "channel_id":  channel_id,
        "message_ts":  None,
        "completed":   False,
        "items": [
            {**item, "done": False, "done_by": None, "done_at": None}
            for item in ONBOARDING_CHECKLIST
        ],
    }

    try:
        msg_text      = _render_onboarding_message(ob)
        result        = _ctx.app.client.chat_postMessage(channel=channel_id, text=msg_text)
        ob["message_ts"] = result["ts"]
        data[key]     = ob
        _save_onboardings(data)
        logger.info(f"✅ Onboarding {client_name} stworzony przez {initiator}, ts={ob['message_ts']}")
    except Exception as e:
        logger.error(f"Błąd tworzenia onboardingu: {e}")
        respond(f"❌ Nie udało się stworzyć onboardingu: {e}")


def _find_active_onboarding_in_channel(channel_id):
    """Zwraca (key, ob) dla aktywnego onboardingu w danym kanale (najnowszy)."""
    data       = _load_onboardings()
    candidates = [
        (k, o) for k, o in data.items()
        if o.get("channel_id") == channel_id and not o.get("completed")
    ]
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    return candidates[0]


def _recover_onboarding_from_thread(channel_id, thread_ts, exclude_ts=None):
    """Gdy brak danych po restarcie, odtwarza stan z historii wątku.
    Czyta 'done N' komendy z odpowiedzi — niezawodne źródło prawdy."""
    try:
        parent = _ctx.app.client.conversations_history(
            channel=channel_id,
            latest=str(float(thread_ts) + 1),
            oldest=str(float(thread_ts) - 1),
            limit=1, inclusive=True,
        )
        msgs = parent.get("messages", [])
        if not msgs:
            return None, None
        msg_text = msgs[0].get("text", "")
        m = re.search(r'Onboarding:\s*\*?(.+?)\*?\n', msg_text)
        if not m:
            return None, None
        client_name = m.group(1).strip()

        replies_result = _ctx.app.client.conversations_replies(
            channel=channel_id, ts=thread_ts, limit=200,
        )
        replies  = replies_result.get("messages", [])[1:]

        done_ids = set()
        for reply in replies:
            if reply.get("bot_id") or reply.get("subtype") == "bot_message":
                continue
            if exclude_ts and reply.get("ts") == exclude_ts:
                continue
            reply_text = reply.get("text", "").lower()
            dm = re.search(r'\bdone\b(.*)', reply_text)
            if not dm:
                continue
            after = dm.group(1)
            if "all" in after:
                done_ids = set(range(1, len(ONBOARDING_CHECKLIST) + 1))
            else:
                for n in re.findall(r'\d+', after):
                    done_ids.add(int(n))

        items = [
            {**item_def, "done": item_def["id"] in done_ids,
             "done_by": None, "done_at": None}
            for item_def in ONBOARDING_CHECKLIST
        ]
        ob = {
            "client_name": client_name,
            "created_at":  datetime.now().isoformat(),
            "created_by":  "recovered",
            "channel_id":  channel_id,
            "message_ts":  thread_ts,
            "completed":   all(i["done"] for i in items),
            "items":       items,
        }
        key  = _onboarding_key(client_name)
        data = _load_onboardings()
        data[key] = ob
        _save_onboardings(data)
        logger.info(f"🔄 Recovery onboarding '{client_name}': {len(done_ids)} punktów done z wątku")
        return key, ob
    except Exception as e:
        logger.error(f"Błąd recovery onboardingu z wątku: {e}")
        return None, None


def _handle_onboarding_done(event, say):
    """Obsługuje 'done N' — działa zarówno w wątku jak i w kanale."""
    text      = (event.get("text") or "").strip().lower()
    thread_ts = event.get("thread_ts")
    channel_id = event.get("channel")
    user_id   = event.get("user")

    if not re.search(r'\bdone\b', text):
        return False

    if thread_ts:
        key, ob = _find_onboarding_by_thread(thread_ts, channel_id, current_ts=event.get("ts"))
    else:
        key, ob = None, None

    if not ob:
        key, ob = _find_active_onboarding_in_channel(channel_id)

    if not ob:
        return False

    after_done      = re.search(r'\bdone\b(.*)', text)
    after_done_text = after_done.group(1) if after_done else ""
    if "all" in after_done_text:
        item_ids = [i["id"] for i in ob["items"] if not i["done"]]
    else:
        item_ids = list(map(int, re.findall(r'\d+', after_done_text)))

    if not item_ids:
        return False

    try:
        ui        = _ctx.app.client.users_info(user=user_id)
        user_name = (ui["user"].get("real_name")
                     or ui["user"].get("profile", {}).get("display_name")
                     or user_id)
    except Exception:
        user_name = user_id

    changed = []
    for item in ob["items"]:
        if item["id"] in item_ids and not item["done"]:
            item["done"]    = True
            item["done_by"] = user_name
            item["done_at"] = datetime.now().isoformat()
            changed.append(item)

    if not changed:
        _ctx.app.client.chat_postMessage(
            channel=channel_id,
            thread_ts=ob["message_ts"],
            text="ℹ️ Te punkty były już odhaczone.",
        )
        return True

    all_done = all(i["done"] for i in ob["items"])
    if all_done:
        ob["completed"]    = True
        ob["completed_at"] = datetime.now().isoformat()

    data      = _load_onboardings()
    data[key] = ob
    _save_onboardings(data)

    new_text = _render_onboarding_message(ob)
    try:
        _ctx.app.client.chat_update(
            channel=channel_id,
            ts=ob["message_ts"],
            text=new_text,
        )
    except Exception as e:
        logger.error(f"Błąd update onboarding msg: {e}")

    names = ", ".join(f"*{i['id']}. {i['name']}*" for i in changed)
    if all_done:
        reply = f"🎉 *{ob['client_name']}* — onboarding 100% ukończony! Super robota!"
    else:
        remaining = sum(1 for i in ob["items"] if not i["done"])
        plural    = 'y' if 2 <= remaining <= 4 else ('ów' if remaining != 1 else '')
        reply     = f"✅ Odhaczone: {names}\nZostało jeszcze: *{remaining}* punkt{plural}"

    _ctx.app.client.chat_postMessage(
        channel=channel_id,
        thread_ts=ob["message_ts"],
        text=reply,
    )
    return True


def check_stale_onboardings():
    """Codziennie rano: pinguje kanał jeśli onboarding trwa >3 dni i nie jest ukończony."""
    data = _load_onboardings()
    if not data:
        return

    now = datetime.now()
    for key, ob in data.items():
        if ob.get("completed"):
            continue
        try:
            created = datetime.fromisoformat(ob["created_at"])
            days_open = (now - created).days
            if days_open >= 3:
                channel_id = ob.get("channel_id")
                if not channel_id:
                    continue
                done_count = sum(1 for i in ob["items"] if i["done"])
                total      = len(ob["items"])
                _ctx.app.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=ob.get("message_ts"),
                    text=(
                        f"⚠️ Onboarding *{ob['client_name']}* trwa już *{days_open} dni* "
                        f"({done_count}/{total} punktów). Sprawdź postęp!"
                    ),
                )
                logger.info(f"⚠️ Stale onboarding ping: {ob['client_name']} ({days_open} dni)")
        except Exception as e:
            logger.error(f"check_stale_onboardings {key}: {e}")
