import os
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pytz
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from anthropic import Anthropic
# Przechowywanie odpowiedzi z check-inów
checkin_responses = {}
# Inicjalizacja Slack App
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

# Inicjalizacja Claude
anthropic = Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))

# Reaguj na wzmianki (@bot)
@app.event("app_mention")
def handle_mention(event, say):
    # Pobierz tekst wiadomości (usuń wzmianke bota)
    user_message = event['text']
    # Usuń <@BOTID> z początku
    user_message = ' '.join(user_message.split()[1:])

    # DODAJ TO ⬇️
    # Komenda testowa
    if "test checkin" in user_message.lower():
        weekly_checkin()
        say("✅ Wysłałem check-iny testowo!")
        return
    # KONIEC ⬆️
    
    # Wyślij "pisze..." indicator
    channel = event['channel']
    ...
    
    # Wyślij "pisze..." indicator
    channel = event['channel']
    thread_ts = event.get('thread_ts', event['ts'])
    
    try:
        # Zapytaj Claude
        message = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )
        
        # Wyślij odpowiedź w tym samym wątku
        response_text = message.content[0].text
        say(text=response_text, thread_ts=thread_ts)
        
    except Exception as e:
        say(text=f"Przepraszam, wystąpił błąd: {str(e)}", thread_ts=thread_ts)

# Reaguj na wiadomości w DM (bez oznaczania)
@app.event("message")
def handle_message_events(body, say, logger):
    logger.info(body)
    event = body["event"]
    
    # Sprawdź czy to odpowiedź na check-in
    if event.get("channel_type") == "im" and event.get("user") in checkin_responses:
        user_message = event.get("text", "")
        checkin_responses[event["user"]].append(user_message)
        say("✅ Dziękuję za odpowiedź! Twój feedback jest dla nas ważny. 🙏")
        return
    event = body["event"]
    
    # Ignoruj wiadomości od botów (żeby nie odpowiadać sam sobie)
    if event.get("bot_id"):
        return
    
    # Ignoruj wiadomości które są wzmiankami (obsługiwane przez app_mention)
    if event.get("subtype") == "bot_message":
        return
        
    user_message = event.get("text", "")
    channel = event["channel"]
    
    try:
        # Zapytaj Claude
        message = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )
        
        response_text = message.content[0].text
        say(text=response_text)
        
    except Exception as e:
        say(text=f"Przepraszam, wystąpił błąd: {str(e)}")

# Funkcja do codziennych podsumowań
def daily_summaries():
    warsaw_tz = pytz.timezone('Europe/Warsaw')
    today = datetime.now(warsaw_tz)
    start_of_day = today.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Pobierz wszystkie kanały gdzie bot jest członkiem
    try:
        result = app.client.conversations_list(types="public_channel,private_channel")
        channels = result["channels"]
        
        for channel in channels:
            if channel.get("is_member"):
                channel_id = channel["id"]
                channel_name = channel["name"]
                
                # Pobierz wiadomości z dzisiaj
                messages_result = app.client.conversations_history(
                    channel=channel_id,
                    oldest=str(int(start_of_day.timestamp()))
                )
                
                messages = messages_result.get("messages", [])
                
                # Tylko jeśli jest 10+ wiadomości
                if len(messages) >= 10:
                    # Przygotuj tekst do podsumowania
                    messages_text = "\n".join([
                        f"{msg.get('user', 'Unknown')}: {msg.get('text', '')}" 
                        for msg in reversed(messages[:50])  # Max 50 wiadomości
                    ])
                    
                    # Poproś Claude o podsumowanie
                    summary = anthropic.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=500,
                        messages=[{
                            "role": "user",
                            "content": f"Zrób krótkie podsumowanie (3-5 zdań) najważniejszych tematów z dzisiejszych rozmów na kanale #{channel_name}:\n\n{messages_text}"
                        }]
                    )
                    
                    summary_text = summary.content[0].text
                    
                    # Wyślij podsumowanie na kanał
                    app.client.chat_postMessage(
                        channel=channel_id,
                        text=f"📊 *Podsumowanie dnia ({today.strftime('%d.%m.%Y')})*\n\n{summary_text}"
                    )
                    
    except Exception as e:
        print(f"Błąd podczas tworzenia podsumowań: {e}")

# Scheduler - codziennie o 17:00
scheduler = BackgroundScheduler(timezone=pytz.timezone('Europe/Warsaw'))
scheduler.add_job(daily_summaries, 'cron', hour=22, minute=29)
scheduler.start()


# Weekly check-in - piątek 16:00
def weekly_checkin():
    warsaw_tz = pytz.timezone('Europe/Warsaw')
    
    try:
        # Pobierz listę wszystkich użytkowników
        result = app.client.users_list()
        users = result["members"]
        
        for user in users:
            # Pomiń boty i deactivated users
            if user.get("is_bot") or user.get("deleted"):
                continue
                
            user_id = user["id"]
            
            # Wyślij DM z pytaniami
            app.client.chat_postMessage(
                channel=user_id,
                text=f"""Cześć! 👋 Czas na weekly check-in!

Odpowiedz na kilka pytań o ten tydzień:

1️⃣ **Jak oceniasz swój tydzień w skali 1-10?**
2️⃣ **Czy miałeś/aś dużo pracy?** (Za dużo / W sam raz / Za mało)
3️⃣ **Jak się czujesz?** (Energetycznie / Normalnie / Zmęczony/a / Wypalony/a)
4️⃣ **Czy czegoś Ci brakuje do lepszej pracy?**
5️⃣ **Co poszło dobrze w tym tygodniu?** 🎉
6️⃣ **Co mogłoby być lepsze?**
7️⃣ **Czy masz jakieś blokery/problemy?**

Napisz swoje odpowiedzi poniżej (możesz w jednej wiadomości lub osobno). Wszystko jest **poufne i anonimowe**! 🔒"""
            )
            
            # Zainicjuj pustą listę odpowiedzi dla użytkownika
            checkin_responses[user_id] = []
            
    except Exception as e:
        print(f"Błąd podczas wysyłania check-inów: {e}")

# Podsumowanie check-inów - poniedziałek 9:00
def checkin_summary():
    warsaw_tz = pytz.timezone('Europe/Warsaw')
    
    if not checkin_responses:
        return
    
    try:
        # Zbierz wszystkie odpowiedzi
        all_responses = "\n\n---\n\n".join([
            f"Osoba {i+1}:\n" + "\n".join(responses)
            for i, responses in enumerate(checkin_responses.values())
            if responses
        ])
        
        if not all_responses:
            return
        
        # Poproś Claude o analizę
        analysis = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{
                "role": "user",
                "content": f"""Przeanalizuj odpowiedzi z weekly check-inu zespołu i stwórz podsumowanie zawierające:

1. ZESPÓŁ W LICZBACH (średnie oceny, nastroje, obciążenie)
2. NAJCZĘSTSZE WYZWANIA (co przeszkadza, blokery)
3. CO IDZIE DOBRZE (pozytywne rzeczy)
4. REKOMENDACJE (co warto poprawić)

Odpowiedzi zespołu:

{all_responses}

Zachowaj pełną anonimowość - nie używaj imion, nie cytuj dosłownie."""
            }]
        )
        
        summary_text = analysis.content[0].text
        
        # Wyślij podsumowanie do Ciebie
        YOUR_USER_ID = "UTE1RN6SJ"  # <-- ZMIEŃ NA SWOJE USER ID!
        
        app.client.chat_postMessage(
            channel=YOUR_USER_ID,
            text=f"""📊 **WEEKLY CHECK-IN - PODSUMOWANIE ZESPOŁU**
            
{summary_text}

---
_Odpowiedzi od {len([r for r in checkin_responses.values() if r])} osób_"""
        )
        
        # Wyczyść odpowiedzi na kolejny tydzień
        checkin_responses.clear()
        
    except Exception as e:
        print(f"Błąd podczas tworzenia podsumowania check-in: {e}")

# Dodaj do schedulera
scheduler.add_job(weekly_checkin, 'cron', hour=22, minute=29)
# scheduler.add_job(checkin_summary, 'cron', day_of_week='mon', hour=9, minute=0)
print(f"✅ Scheduler załadowany! Jobs: {len(scheduler.get_jobs())}")
print("✅ Scheduler wystartował!")
# Uruchom bota
handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
print("⚡️ Bot działa!")
handler.start()
