"""Weekly check-in: wysyłka, reminders i podsumowanie."""
import logging

import _ctx
from config.constants import TEAM_MEMBERS

logger = logging.getLogger(__name__)


def weekly_checkin():
    try:
        logger.info("🔥 ROZPOCZYNAM WEEKLY CHECK-IN!")

        _ctx.checkin_responses.clear()

        sent_count = 0
        for member in TEAM_MEMBERS:
            user_id   = member["slack_id"]
            user_name = member["name"]
            try:
                dm = _ctx.app.client.conversations_open(users=user_id)["channel"]["id"]
                _ctx.app.client.chat_postMessage(
                    channel=dm,
                    text=(
                        f"Cześć {user_name}! 👋 Czas na *weekly check-in*!\n\n"
                        "Odpowiedz na kilka pytań o ten tydzień:\n\n"
                        "1️⃣ Jak oceniasz swój tydzień w skali *1-10*?\n"
                        "2️⃣ Czy miałeś/aś dużo pracy? _(Za dużo / W sam raz / Za mało)_\n"
                        "3️⃣ Jak się czujesz? _(Energetycznie / Normalnie / Zmęczony·a / Wypalony·a)_\n"
                        "4️⃣ Czy czegoś Ci brakuje do lepszej pracy?\n"
                        "5️⃣ Co poszło dobrze w tym tygodniu? 🎉\n"
                        "6️⃣ Co mogłoby być lepsze?\n"
                        "7️⃣ Czy masz jakieś blokery lub problemy?\n\n"
                        "Możesz pisać w jednej wiadomości lub osobno. "
                        "Na końcu napisz *gotowe* żebym zapisał Twoje odpowiedzi. "
                        "Wszystko jest *poufne i anonimowe* 🔒"
                    ),
                )
                _ctx.checkin_responses[user_id] = {"messages": [], "done": False, "name": user_name}
                sent_count += 1
                logger.info(f"✉️ Check-in wysłany → {user_name} ({user_id})")
            except Exception as e:
                logger.error(f"Błąd wysyłki check-in do {user_name}: {e}")

        logger.info(f"✅ Weekly check-in wysłany do {sent_count}/{len(TEAM_MEMBERS)} osób")

    except Exception as e:
        logger.error(f"Błąd podczas wysyłania check-inów: {e}")


def send_checkin_reminders():
    """Piątek 17:30 — przypomnienie dla osób bez odpowiedzi lub bez potwierdzenia."""
    if not _ctx.checkin_responses:
        logger.info("Checkin reminders: brak aktywnych check-inów, pomijam.")
        return

    no_answer   = [(uid, v) for uid, v in _ctx.checkin_responses.items() if not v["messages"]]
    in_progress = [(uid, v) for uid, v in _ctx.checkin_responses.items() if v["messages"] and not v["done"]]

    for uid, v in no_answer:
        try:
            dm = _ctx.app.client.conversations_open(users=uid)["channel"]["id"]
            _ctx.app.client.chat_postMessage(
                channel=dm,
                text=(
                    f"👋 Hej {v['name']}! Widzę że nie miałeś/aś jeszcze czasu na check-in. "
                    "Masz chwilę? 😊 Odpowiedz na pytania i napisz *gotowe* kiedy skończysz."
                ),
            )
            logger.info(f"📨 Checkin reminder (brak odp) → {v['name']}")
        except Exception as e:
            logger.error(f"Checkin reminder no_answer {uid}: {e}")

    for uid, v in in_progress:
        try:
            dm = _ctx.app.client.conversations_open(users=uid)["channel"]["id"]
            _ctx.app.client.chat_postMessage(
                channel=dm,
                text=(
                    f"✍️ {v['name']}, widzę że zacząłeś/aś check-in — super! "
                    "Napisz *gotowe* żebym oficjalnie zapisał Twoje odpowiedzi 👍"
                ),
            )
            logger.info(f"📨 Checkin reminder (w trakcie) → {v['name']}")
        except Exception as e:
            logger.error(f"Checkin reminder in_progress {uid}: {e}")

    logger.info(
        f"Checkin reminders wysłane: {len(no_answer)} bez odp, {len(in_progress)} w trakcie"
    )


def checkin_summary():
    if not _ctx.checkin_responses:
        return

    try:
        responded = {uid: v for uid, v in _ctx.checkin_responses.items() if v.get("messages")}
        no_answer = [v["name"] for uid, v in _ctx.checkin_responses.items() if not v.get("messages")]

        if not responded:
            logger.info("Checkin summary: brak odpowiedzi, pomijam.")
            return

        all_responses = "\n\n---\n\n".join([
            f"Osoba {i+1}:\n" + "\n".join(v["messages"])
            for i, v in enumerate(responded.values())
        ])

        analysis = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{
                "role": "user",
                "content": (
                    "Przeanalizuj odpowiedzi z weekly check-inu zespołu i stwórz podsumowanie:\n\n"
                    "1. ZESPÓŁ W LICZBACH (średnie oceny, nastroje, obciążenie)\n"
                    "2. NAJCZĘSTSZE WYZWANIA (co przeszkadza, blokery)\n"
                    "3. CO IDZIE DOBRZE (pozytywne rzeczy)\n"
                    "4. REKOMENDACJE (co warto poprawić)\n\n"
                    f"Odpowiedzi zespołu:\n\n{all_responses}\n\n"
                    "Zachowaj pełną anonimowość — nie używaj imion, nie cytuj dosłownie."
                ),
            }],
        )

        summary_text = analysis.content[0].text

        confirmed_names = [v["name"] for v in responded.values() if v.get("done")]
        partial_names   = [v["name"] for v in responded.values() if not v.get("done")]

        footer_parts = [f"_Odpowiedzi od {len(responded)}/{len(TEAM_MEMBERS)} osób_"]
        if confirmed_names:
            footer_parts.append(f"✅ Potwierdzone: {', '.join(confirmed_names)}")
        if partial_names:
            footer_parts.append(f"✍️ Częściowe (bez 'gotowe'): {', '.join(partial_names)}")
        if no_answer:
            footer_parts.append(f"⏰ Brak odpowiedzi: {', '.join(no_answer)}")

        YOUR_USER_ID = "UTE1RN6SJ"
        dm = _ctx.app.client.conversations_open(users=YOUR_USER_ID)["channel"]["id"]
        _ctx.app.client.chat_postMessage(
            channel=dm,
            text=(
                f"📊 *WEEKLY CHECK-IN — PODSUMOWANIE ZESPOŁU*\n\n"
                f"{summary_text}\n\n"
                f"---\n" + "\n".join(footer_parts)
            ),
        )

        _ctx.checkin_responses.clear()
        logger.info("✅ Checkin summary wysłany i dane wyczyszczone.")

    except Exception as e:
        logger.error(f"Błąd podczas tworzenia podsumowania check-in: {e}")
