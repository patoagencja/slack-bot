"""Team availability + employee request system + unified DM handler."""
import os
import json
import logging
import re as _re
from datetime import datetime, timedelta

import _ctx
from config.constants import (
    TEAM_MEMBERS, AVAILABILITY_FILE, REQUESTS_FILE,
    ABSENCE_KEYWORDS, EMPLOYEE_MSG_KEYWORDS,
    TYPE_LABELS_ABSENCE, REQUEST_CATEGORY_LABELS,
)

logger = logging.getLogger(__name__)


# ── TEAM LOOKUP ───────────────────────────────────────────────────────────────

def find_team_member(name_hint):
    """Szuka osoby w teamie po imieniu/aliasie (case-insensitive). Zwraca dict lub None."""
    if not name_hint:
        return None
    needle = name_hint.lower().strip()
    for m in TEAM_MEMBERS:
        if needle in m["aliases"]:
            return m
    for m in TEAM_MEMBERS:
        for alias in m["aliases"]:
            if alias.startswith(needle) or needle.startswith(alias):
                return m
    return None


def get_team_context_str():
    """Zwraca opis teamu dla promptów Claude."""
    return "\n".join(f"  - {m['name']} ({m['role']})" for m in TEAM_MEMBERS)


# ── AVAILABILITY FILE HELPERS ─────────────────────────────────────────────────

def _load_availability():
    try:
        if os.path.exists(AVAILABILITY_FILE):
            with open(AVAILABILITY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_availability(entries):
    try:
        os.makedirs(os.path.dirname(AVAILABILITY_FILE), exist_ok=True)
        cutoff  = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
        entries = [e for e in entries if e.get("date", "2000-01-01") >= cutoff]
        with open(AVAILABILITY_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ Błąd zapisu availability: {e}")


def _parse_availability_with_claude(user_message, user_name):
    """Użyj Claude do sparsowania wiadomości o nieobecności. Zwraca listę {date, type, details} lub None."""
    today_str    = datetime.now().strftime('%Y-%m-%d')
    today_weekday = datetime.now().strftime('%A')

    prompt = f"""Analizujesz wiadomość od pracownika polskiej agencji o jego dostępności/nieobecności.

Dzisiaj: {today_str} ({today_weekday}), rok {datetime.now().year}

Wiadomość od {user_name}: "{user_message}"

Typy nieobecności:
- "absent" = cały dzień nieobecny/a (wyjazd, urlop, L4, delegacja, konferencja itp.)
- "morning_only" = tylko rano (do ~12:00)
- "afternoon_only" = tylko po południu (od ~12:00)
- "late_start" = późniejszy start
- "early_end" = wcześniejsze wyjście
- "remote" = praca zdalna (dostępny/a, inna lokalizacja)
- "partial" = częściowo dostępny/a

FORMATY DAT które musisz obsłużyć:
- "jutro", "pojutrze", "w piątek", "w przyszłym tygodniu"
- "5 marca", "05.03", "05.03.25", "05.03.2025"
- ZAKRES: "05.03-23.03", "5-23 marca", "od 5 do 23 marca", "od 05.03 do 23.03" → wygeneruj KAŻDY dzień roboczy z zakresu (pomiń soboty i niedziele)
- Wiele dat: "wtorek i środa", "poniedziałek, wtorek"
- Rok domyślny gdy brak: {datetime.now().year} (jeśli data już minęła → następny rok)

WAŻNE: wyjazd, delegacja, konferencja, szkolenie = typ "absent".

Odpowiedz TYLKO JSON:
{{
  "is_availability": true/false,
  "entries": [
    {{"date": "YYYY-MM-DD", "type": "absent", "details": "opis po polsku, np. wyjazd służbowy"}}
  ]
}}
Jeśli brak konkretnych dat (tylko ogólna info bez terminu): {{"is_availability": false, "entries": []}}"""

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if m:
            data = json.loads(m.group())
            if data.get("is_availability") and data.get("entries"):
                return data["entries"]
    except Exception as e:
        logger.error(f"❌ Błąd parsowania availability: {e}")
    return None


def save_availability_entry(user_id, user_name, entries):
    """Zapisuje wpisy nieobecności (nadpisuje jeśli już był wpis na ten dzień)."""
    all_entries = _load_availability()
    saved_dates = []
    for entry in entries:
        all_entries = [e for e in all_entries
                       if not (e["user_id"] == user_id and e["date"] == entry["date"])]
        all_entries.append({
            "user_id":     user_id,
            "user_name":   user_name,
            "date":        entry["date"],
            "type":        entry["type"],
            "details":     entry.get("details", ""),
            "recorded_at": datetime.now().isoformat(),
        })
        saved_dates.append(entry["date"])
    _save_availability(all_entries)
    return saved_dates


def get_availability_for_date(target_date):
    """Zwraca listę nieobecności na dany dzień."""
    return [e for e in _load_availability() if e.get("date") == target_date]


def remove_availability_entries(user_id: str, date_from: str = None, date_to: str = None):
    """Usuwa wpisy nieobecności dla użytkownika. Jeśli brak dat → usuwa wszystkie przyszłe."""
    all_entries = _load_availability()
    today = datetime.now().strftime('%Y-%m-%d')
    before = len(all_entries)
    if date_from and date_to:
        all_entries = [e for e in all_entries
                       if not (e["user_id"] == user_id and date_from <= e["date"] <= date_to)]
    else:
        all_entries = [e for e in all_entries
                       if not (e["user_id"] == user_id and e["date"] >= today)]
    removed = before - len(all_entries)
    _save_availability(all_entries)
    return removed


def _classify_absence_message(text: str, reporter_name: str, msg_date: str = None):
    """
    Klasyfikuje wiadomość o nieobecności — zwraca absent_person + entries.
    Używana przez sync_availability_from_slack do wykrycia czy wiadomość
    dotyczy nadawcy czy kogoś innego (np. 'Piotrka nie będzie w piątek').
    msg_date: data wiadomości w formacie YYYY-MM-DD (używana jako kontekst "dziś" przy parsowaniu)
    """
    ref_date      = datetime.strptime(msg_date, '%Y-%m-%d') if msg_date else datetime.now()
    today_str     = ref_date.strftime('%Y-%m-%d')
    today_weekday = ref_date.strftime('%A')
    current_year  = ref_date.year

    prompt = f"""Analizujesz wiadomość od pracownika "{reporter_name}".

WIADOMOŚĆ: "{text}"
DZIŚ (data wysłania wiadomości): {today_str} ({today_weekday}), rok {current_year}

CZY nieobecność dotyczy nadawcy ({reporter_name}) czy INNEJ osoby?

Przykłady (nadawca = "Daniel"):
  "Piotrek nie będzie w piątek" → absent_person: "Piotrek"
  "jutro mnie nie będzie"       → absent_person: null
  "Kasia ma urlop 5-10 marca"   → absent_person: "Kasia"

Typy: absent / morning_only / afternoon_only / late_start / early_end / remote / partial
Zakresy dat → wygeneruj KAŻDY dzień roboczy z zakresu (pomiń sob/niedz), nawet jeśli data jest w przeszłości.
Rok domyślny: {current_year}.

Odpowiedz TYLKO JSON:
{{
  "absent_person": <"Imię" jeśli inna osoba, null jeśli sam nadawca>,
  "absence_entries": [{{"date": "YYYY-MM-DD", "type": "absent", "details": "opis pl"}}]
}}
Jeśli brak konkretnych dat: {{"absent_person": null, "absence_entries": []}}"""

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.warning(f"_classify_absence_message error: {e}")
    return None


def sync_availability_from_slack():
    """
    Odczytuje historię DM z ostatnich 14 dni dla każdego członka teamu
    i rekonstruuje dane nieobecności. Wywoływane przy starcie i przed raportem.
    Poprawnie obsługuje wiadomości o nieobecności innej osoby (np. Danio pisze
    'Piotrka nie będzie' → zapisuje pod Piotrkiem, nie Danio).
    """
    cutoff_ts = str((datetime.now() - timedelta(days=30)).timestamp())
    synced_total = 0
    for member in TEAM_MEMBERS:
        try:
            dm = _ctx.app.client.conversations_open(users=member["slack_id"])
            channel_id = dm["channel"]["id"]
            history = _ctx.app.client.conversations_history(
                channel=channel_id, oldest=cutoff_ts, limit=100
            )
            for msg in history.get("messages", []):
                if msg.get("bot_id") or msg.get("subtype"):
                    continue
                text = msg.get("text", "").strip()
                if len(text) < 8:
                    continue
                if not any(kw in text.lower() for kw in ABSENCE_KEYWORDS):
                    continue

                # Użyj daty wiadomości jako kontekstu, żeby relative daty ("jutro") były poprawne
                msg_ts   = msg.get("ts")
                msg_date = datetime.fromtimestamp(float(msg_ts)).strftime('%Y-%m-%d') if msg_ts else None
                classified = _classify_absence_message(text, member["name"], msg_date=msg_date)
                if not classified:
                    continue
                entries = classified.get("absence_entries", [])
                if not entries:
                    continue

                absent_person = (classified.get("absent_person") or "").strip() or None
                if absent_person:
                    # Nieobecność dotyczy kogoś innego — znajdź właściwą osobę
                    target = find_team_member(absent_person)
                    if target:
                        save_availability_entry(target["slack_id"], target["name"], entries)
                        synced_total += len(entries)
                        logger.info(
                            f"sync: saved {len(entries)} entries for {target['name']}"
                            f" (reported by {member['name']}): {text[:80]!r}"
                        )
                    else:
                        logger.info(
                            f"sync: unknown absent_person={absent_person!r}"
                            f" in msg from {member['name']}: {text[:80]!r}"
                        )
                else:
                    save_availability_entry(member["slack_id"], member["name"], entries)
                    synced_total += len(entries)
                    logger.info(
                        f"sync: saved {len(entries)} entries for {member['name']}"
                        f" from msg: {text[:80]!r}"
                    )
        except Exception as e:
            logger.warning(f"sync_availability_from_slack [{member['name']}]: {e}")
    if synced_total:
        logger.info(f"🔄 Synced {synced_total} absence entries from Slack history")


def _next_workday(from_date=None):
    """Zwraca następny dzień roboczy (pomiń weekend)."""
    d = from_date or datetime.now()
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d = d + timedelta(days=1)
    return d


def _format_availability_summary(entries, date_label):
    """Formatuje czytelne podsumowanie dla Daniela — pokazuje cały team."""
    TYPE_LABELS = {
        "absent":           "❌ Nieobecna/y",
        "morning_only":     "🌅 Tylko rano",
        "afternoon_only":   "🌆 Tylko po południu",
        "late_start":       "🕙 Późniejszy start",
        "early_end":        "🏃 Wcześniejsze wyjście",
        "remote":           "🏠 Zdalnie",
        "partial":          "⏰ Częściowo",
    }

    absent_ids    = {e["user_id"]: e for e in entries}
    absent_lines  = []
    present_names = []

    for m in TEAM_MEMBERS:
        if m["slack_id"] in absent_ids:
            e     = absent_ids[m["slack_id"]]
            label = TYPE_LABELS.get(e.get("type", "absent"), "⚠️ Ograniczona dostępność")
            line  = f"• *{m['name']}* ({m['role']}) — {label}"
            if e.get("details"):
                line += f"\n  _{e['details']}_"
            absent_lines.append(line)
        else:
            present_names.append(f"{m['name']}")

    msg = f"📅 *Dostępność teamu — {date_label}:*\n\n"

    if absent_lines:
        msg += "\n".join(absent_lines) + "\n"
    else:
        msg += "✅ Wszyscy w biurze!\n"

    if present_names:
        msg += f"\n✅ *W pracy:* {', '.join(present_names)}"

    return msg


def send_daily_team_availability():
    """Wysyła na #zarzondpato o 17:00: dostępność jutro + otwarte prośby teamu."""
    try:
        sync_availability_from_slack()
        tomorrow       = _next_workday()
        tomorrow_str   = tomorrow.strftime('%Y-%m-%d')
        tomorrow_label = tomorrow.strftime('%A %d.%m.%Y')

        abs_entries = get_availability_for_date(tomorrow_str)
        abs_msg     = _format_availability_summary(abs_entries, tomorrow_label)

        pending = get_pending_requests()
        if pending:
            req_msg = f"\n\n━━━━━━━━━━━━━━━━━━━━━━\n\n" + _format_requests_list(pending)
        else:
            req_msg = "\n\n━━━━━━━━━━━━━━━━━━━━━━\n\n✅ Brak otwartych próśb."

        _ctx.app.client.chat_postMessage(channel="C0AJ4HBS94G", text=abs_msg + req_msg)
        logger.info(f"✅ Team summary wysłane na #zarzondpato (nieobecności: {len(abs_entries)}, prośby: {len(pending)})")
    except Exception as e:
        logger.error(f"❌ Błąd send_daily_team_availability: {e}")


# ── REQUESTS FILE HELPERS ─────────────────────────────────────────────────────

def _load_requests():
    try:
        if os.path.exists(REQUESTS_FILE):
            with open(REQUESTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_requests(requests):
    try:
        os.makedirs(os.path.dirname(REQUESTS_FILE), exist_ok=True)
        with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
            json.dump(requests, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ Błąd zapisu requests: {e}")


def _next_request_id():
    requests = _load_requests()
    if not requests:
        return 1
    return max(r.get("id", 0) for r in requests) + 1


def save_request(user_id, user_name, category, summary, original_message):
    """Zapisuje nową prośbę i zwraca jej ID."""
    requests = _load_requests()
    req_id   = _next_request_id()
    requests.append({
        "id":               req_id,
        "user_id":          user_id,
        "user_name":        user_name,
        "category":         category,
        "summary":          summary,
        "original_message": original_message,
        "status":           "pending",
        "created_at":       datetime.now().isoformat(),
        "closed_at":        None,
    })
    _save_requests(requests)
    return req_id


def close_request(req_id):
    """Zamknij prośbę po ID. Zwraca dict prośby lub None jeśli nie znaleziono."""
    requests = _load_requests()
    found    = None
    for r in requests:
        if r.get("id") == req_id and r.get("status") == "pending":
            r["status"]    = "done"
            r["closed_at"] = datetime.now().isoformat()
            found = r
            break
    if found:
        _save_requests(requests)
    return found


def get_pending_requests():
    """Zwraca wszystkie otwarte prośby."""
    return [r for r in _load_requests() if r.get("status") == "pending"]


def _format_requests_list(requests):
    """Formatuje listę próśb dla Daniela."""
    if not requests:
        return "✅ Brak otwartych próśb — wszystko załatwione!"
    msg = f"📋 *Otwarte prośby teamu ({len(requests)}):*\n\n"
    for r in requests:
        cat_label = REQUEST_CATEGORY_LABELS.get(r.get("category", "inne"), "📌 Inne")
        created   = datetime.fromisoformat(r["created_at"]).strftime('%d.%m %H:%M')
        msg += f"*#{r['id']}* — *{r['user_name']}* [{created}]\n"
        msg += f"  {cat_label}: {r['summary']}\n\n"
    msg += "_Zamknij: `@Sebol zamknij #N`_"
    return msg


# ── UNIFIED EMPLOYEE DM HANDLER ───────────────────────────────────────────────

def handle_employee_dm(user_id, user_name, user_message, say):
    """
    Każdy DM jedzie przez Claude — żadnych keywordów.
    Claude sam ocenia: nieobecność / prośba do szefa / zwykła rozmowa.
    Zwraca True jeśli obsłużono (nieobecność lub prośba), False = chat.
    """
    if len(user_message.strip()) < 8:
        return False

    today_str    = datetime.now().strftime('%Y-%m-%d')
    today_weekday = datetime.now().strftime('%A')
    current_year  = datetime.now().year

    team_ctx = get_team_context_str()

    prompt = f"""Przetwórz wiadomość od pracownika agencji marketingowej Pato.

NADAWCA: {user_name}
WIADOMOŚĆ: "{user_message}"
DZIŚ: {today_str} ({today_weekday}), rok {current_year}

ZESPÓŁ PATO (wszyscy pracownicy):
{team_ctx}

═══ KROK 1: KTO JEST NIEOBECNY? ═══
Przeczytaj wiadomość. Czy nieobecność dotyczy {user_name} (piszącego), czy INNEJ osoby z teamu?

Przykłady (nadawca = "Daniel"):
  "Paulina wyjezdza 1-8 marca"           → absent_person: "Paulina"
  "Piotrek nie bedzie w piatek"           → absent_person: "Piotr"
  "Kasia ma urlop w przyszlym tygodniu"   → absent_person: "Kasia"
  "jutro mnie nie bedzie"                 → absent_person: null
  "mam wyjazd 5-10 marca"                → absent_person: null
  "biorę urlop w maju"                    → absent_person: null

Zasada: jeśli podmiotem zdania jest inne imię niż {user_name} → wpisz to imię. Jeśli {user_name} mówi o sobie → null.

═══ KROK 2: TYP WIADOMOŚCI ═══
"absence" — informacja o niedostępności (swojej lub kogoś innego).
"request" — prośba do szefa wymagająca decyzji/działania.
  Uwaga: żarty i casual ("czy mogę iść na kawę") = NIE request, to chat.
"chat" — wszystko inne.

═══ KROK 3: DLA "absence" — daty ═══
Typy: absent / morning_only / afternoon_only / late_start / early_end / remote / partial
Formaty dat: jutro, pojutrze, "w piątek", "5 marca", zakresy "5-23 marca" → KAŻDY dzień roboczy (pomiń sob/niedz).
Rok domyślny: {current_year}.

Odpowiedz TYLKO JSON:
{{
  "absent_person": <"Imie" jeśli inna osoba, null jeśli sam nadawca>,
  "type": "absence" | "request" | "chat",
  "absence_has_dates": true/false,
  "absence_entries": [{{"date": "YYYY-MM-DD", "type": "absent", "details": "opis pl"}}],
  "request_category": "urlop|zakup|dostep|spotkanie|problem|pytanie|inne",
  "request_summary": "Krótki opis prośby po polsku (max 1 zdanie)"
}}"""

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not m:
            return False
        data         = json.loads(m.group())
        msg_type     = data.get("type", "chat")
        absent_person = (data.get("absent_person") or "").strip() or None
        logger.info(f"🤖 DM classify [{user_name}]: type={msg_type} absent_person={absent_person!r}")

        # ── NIEOBECNOŚĆ ──
        if msg_type == "absence":
            if absent_person:
                member = find_team_member(absent_person)
                if member:
                    absent_name = member["name"]
                    absent_uid  = member["slack_id"]
                else:
                    absent_name = absent_person
                    absent_uid  = f"reported_{absent_name.lower()}"
                reporter_suffix  = f" _(zgłoszone przez {user_name})_"
                confirm_msg_prefix = f"✅ Zapisałem nieobecność *{absent_name}*!"
                no_date_msg = f"📅 Rozumiem, że *{absent_name}* będzie niedostępny/a — kiedy dokładnie? Podaj termin to od razu zapiszę. 👍"
            else:
                absent_name      = user_name
                absent_uid       = user_id
                reporter_suffix  = ""
                confirm_msg_prefix = "✅ Zapisałem!"
                no_date_msg = "📅 Rozumiem, że będziesz niedostępny/a — kiedy dokładnie? Podaj termin (np. *'5-23 marca'* albo *'jutro'*) to od razu zapiszę. 👍"

            if not data.get("absence_has_dates", True):
                say(no_date_msg)
                return True

            entries = data.get("absence_entries", [])
            if not entries:
                say(no_date_msg)
                return True

            saved_dates = save_availability_entry(absent_uid, absent_name, entries)
            if not saved_dates:
                return False

            if len(saved_dates) == 1:
                date_fmt  = datetime.strptime(saved_dates[0], '%Y-%m-%d').strftime('%A %d.%m')
                say(f"{confirm_msg_prefix} *{date_fmt}* 👍")
                entry     = next((e for e in entries if e["date"] == saved_dates[0]), entries[0])
                type_label = TYPE_LABELS_ABSENCE.get(entry.get("type", "absent"), "⚠️ Nieobecność")
                notif = f"📅 *{absent_name}* — {type_label} ({date_fmt}){reporter_suffix}"
                if entry.get("details"):
                    notif += f"\n_{entry['details']}_"
            else:
                dates_fmt = ", ".join(datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m') for d in saved_dates)
                say(f"{confirm_msg_prefix} *{dates_fmt}* ({len(saved_dates)} dni) 👍")
                notif = f"📅 *{absent_name}* — nieobecny/a: {dates_fmt}{reporter_suffix}"

            try:
                _ctx.app.client.chat_postMessage(channel="C0AJ4HBS94G", text=notif)
            except Exception as _e:
                logger.error(f"❌ Błąd powiadomienia #zarzondpato: {_e}")
            logger.info(f"📅 Availability: {absent_name} → {saved_dates} (zgłoszone przez {user_name})")
            return True

        # ── PROŚBA ──
        elif msg_type == "request":
            category  = data.get("request_category", "inne")
            summary   = data.get("request_summary", user_message[:100])
            req_id    = save_request(user_id, user_name, category, summary, user_message)
            cat_label = REQUEST_CATEGORY_LABELS.get(category, "📌 Inne")
            say(f"✅ Zapisałem Twoją prośbę *#{req_id}* 👍\n_{summary}_")
            try:
                _ctx.app.client.chat_postMessage(
                    channel="C0AJ4HBS94G",
                    text=f"📋 *Nowa prośba #{req_id}* — *{user_name}*\n{cat_label}: {summary}\n_Zamknij: `@Sebol zamknij #{req_id}`_"
                )
            except Exception as _e:
                logger.error(f"❌ Błąd powiadomienia #zarzondpato: {_e}")
            logger.info(f"📋 Request #{req_id}: {user_name} → {category}: {summary}")
            return True

        return False

    except Exception as e:
        logger.error(f"❌ Błąd handle_employee_dm: {e}")
        return False
