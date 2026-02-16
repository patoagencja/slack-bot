import os
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pytz
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from anthropic import Anthropic

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
scheduler.add_job(daily_summaries, 'cron', hour=15, minute=38)
scheduler.start()

# Uruchom bota
handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
print("⚡️ Bot działa!")
handler.start()
