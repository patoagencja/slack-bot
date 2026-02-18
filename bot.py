import os
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pytz
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from anthropic import Anthropic
import logging
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from datetime import datetime, timedelta
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# Przechowywanie odpowiedzi z check-inów
checkin_responses = {}
# Historia konwersacji dla każdego użytkownika
conversation_history = {}
# Inicjalizacja Slack App
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

# Inicjalizacja Claude
anthropic = Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))
# Inicjalizacja Meta Ads API
try:
    FacebookAdsApi.init(access_token=os.environ.get("META_ACCESS_TOKEN"))
    meta_ad_account_id = os.environ.get("META_AD_ACCOUNT_ID")
except Exception as e:
    logger.error(f"Błąd inicjalizacji Meta Ads API: {e}")
    meta_ad_account_id = None
# Funkcja do pobierania danych z Meta Ads
def get_meta_ads_stats(days_back=1):
    """Pobierz statystyki kampanii z ostatnich X dni"""
    if not meta_ad_account_id:
        return "Meta Ads API nie jest skonfigurowane."
    
    try:
        account = AdAccount(meta_ad_account_id)
        
        # Oblicz daty
        today = datetime.now()
        since = (today - timedelta(days=days_back)).strftime('%Y-%m-%d')
        until = today.strftime('%Y-%m-%d')
        
        # Pobierz kampanie
        campaigns = account.get_campaigns(fields=[
            'name',
            'status',
            'objective'
        ])
        
        # Pobierz insights (statystyki)
        insights = account.get_insights(params={
            'time_range': {'since': since, 'until': until},
            'level': 'campaign',
            'fields': [
                'campaign_name',
                'spend',
                'impressions',
                'clicks',
                'ctr',
                'cpc',
                'cpp'
            ]
        })
        
        if not insights:
            return f"Brak danych za okres {since} - {until}"
        
        # Formatuj odpowiedź
        result = f"📊 **Statystyki Meta Ads** ({since} - {until})\n\n"
        
        total_spend = 0
        total_clicks = 0
        total_impressions = 0
        
        for insight in insights:
            campaign_name = insight.get('campaign_name', 'Nieznana kampania')
            spend = float(insight.get('spend', 0))
            clicks = int(insight.get('clicks', 0))
            impressions = int(insight.get('impressions', 0))
            ctr = float(insight.get('ctr', 0))
            cpc = float(insight.get('cpc', 0))
            
            total_spend += spend
            total_clicks += clicks
            total_impressions += impressions
            
            result += f"**{campaign_name}**\n"
            result += f"• Wydane: {spend:.2f} PLN\n"
            result += f"• Kliknięcia: {clicks:,}\n"
            result += f"• Wyświetlenia: {impressions:,}\n"
            result += f"• CTR: {ctr:.2f}%\n"
            result += f"• CPC: {cpc:.2f} PLN\n\n"
        
        result += f"**PODSUMOWANIE:**\n"
        result += f"💰 Łączny wydatek: {total_spend:.2f} PLN\n"
        result += f"👆 Łączne kliknięcia: {total_clicks:,}\n"
        result += f"👁️ Łączne wyświetlenia: {total_impressions:,}\n"
        
        return result
        
    except Exception as e:
        logger.error(f"Błąd pobierania danych Meta Ads: {e}")
        return f"Błąd: {str(e)}"
# Funkcje do zarządzania historią konwersacji
def get_conversation_history(user_id):
    """Pobierz historię z pamięci (lub pusta lista)"""
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    return conversation_history[user_id]

def save_message_to_history(user_id, role, content):
    """Zapisz wiadomość i ogranicz do ostatnich 100"""
    history = get_conversation_history(user_id)
    history.append({"role": role, "content": content})
    
    # Ogranicz do ostatnich 100 wiadomości
    if len(history) > 100:
        conversation_history[user_id] = history[-100:]
# Reaguj na wzmianki (@bot)
# Narzędzie Meta Ads dla Claude
def meta_ads_tool(date_from=None, date_to=None, campaign_name=None, metrics=None):
    """
    Pobiera dane z Meta Ads API.
    
    Args:
        date_from: Data początkowa w formacie YYYY-MM-DD (opcjonalne, domyślnie wczoraj)
        date_to: Data końcowa w formacie YYYY-MM-DD (opcjonalne, domyślnie dzisiaj)
        campaign_name: Nazwa kampanii do filtrowania (opcjonalne)
        metrics: Lista metryk do pobrania (opcjonalne)
    
    Returns:
        JSON ze statystykami kampanii
    """
    if not meta_ad_account_id:
        return {"error": "Meta Ads API nie jest skonfigurowane."}
    
    try:
        from datetime import datetime, timedelta
        
        # Domyślne daty
        if not date_to:
            date_to = datetime.now().strftime('%Y-%m-%d')
        if not date_from:
            date_from = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        account = AdAccount(meta_ad_account_id)
        
        # Domyślne metryki
        if not metrics:
            metrics = [
                'campaign_name',
                'spend',
                'impressions',
                'clicks',
                'ctr',
                'cpc',
                'cpp',
                'reach',
                'frequency'
            ]
        
        # Pobierz insights
        insights = account.get_insights(params={
            'time_range': {'since': date_from, 'until': date_to},
            'level': 'campaign',
            'fields': metrics
        })
        
        if not insights:
            return {"message": f"Brak danych za okres {date_from} - {date_to}"}
        
        # Konwertuj do listy słowników
        campaigns_data = []
        for insight in insights:
            campaign_data = {}
            for metric in metrics:
                value = insight.get(metric)
                if value is not None:
                    # Konwertuj do odpowiednich typów
                    if metric in ['spend', 'cpc', 'cpp', 'ctr', 'frequency']:
                        campaign_data[metric] = float(value)
                    elif metric in ['impressions', 'clicks', 'reach']:
                        campaign_data[metric] = int(value)
                    else:
                        campaign_data[metric] = str(value)
            
            # Filtruj po nazwie kampanii jeśli podano
            if campaign_name:
                if campaign_name.lower() in campaign_data.get('campaign_name', '').lower():
                    campaigns_data.append(campaign_data)
            else:
                campaigns_data.append(campaign_data)
        
        return {
            "date_from": date_from,
            "date_to": date_to,
            "campaigns": campaigns_data,
            "total_campaigns": len(campaigns_data)
        }
        
    except Exception as e:
        logger.error(f"Błąd pobierania danych Meta Ads: {e}")
        return {"error": str(e)}


# Reaguj na wzmianki (@bot)
@app.event("app_mention")
def handle_mention(event, say):
    user_message = event['text']
    user_message = ' '.join(user_message.split()[1:])  # Usuń wzmianke bota
    
    channel = event['channel']
    thread_ts = event.get('thread_ts', event['ts'])
    
    # Definicja narzędzia dla Claude
    tools = [
        {
            "name": "get_meta_ads_data",
            "description": "Pobiera statystyki kampanii reklamowych z Meta Ads (Facebook Ads). Użyj tego narzędzia gdy użytkownik pyta o kampanie reklamowe, wydatki, wyniki, CTR, CPC lub inne metryki z Facebook Ads.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "date_from": {
                        "type": "string",
                        "description": "Data początkowa w formacie YYYY-MM-DD. Np. 'wczoraj' = dzisiejsza data minus 1 dzień, 'ostatni tydzień' = 7 dni wstecz."
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Data końcowa w formacie YYYY-MM-DD. Domyślnie dzisiaj."
                    },
                    "campaign_name": {
                        "type": "string",
                        "description": "Nazwa kampanii do wyszukania (opcjonalne). Może być częściowa nazwa."
                    },
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista metryk do pobrania. Domyślnie: campaign_name, spend, impressions, clicks, ctr, cpc, cpp, reach, frequency"
                    }
                },
                "required": []
            }
        }
    ]
    
    try:
        # Zapytaj Claude z narzędziami
        messages = [{"role": "user", "content": user_message}]
        
        # Pętla dla tool use (Claude może wielokrotnie używać narzędzi)
        while True:
            response = anthropic.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                tools=tools,
                messages=messages
            )
            
            # Sprawdź czy Claude chce użyć narzędzia
            if response.stop_reason == "tool_use":
                # Claude wywołał narzędzie
                tool_use_block = next(block for block in response.content if block.type == "tool_use")
                tool_name = tool_use_block.name
                tool_input = tool_use_block.input
                
                logger.info(f"Claude wywołał narzędzie: {tool_name} z parametrami: {tool_input}")
                
                # Wywołaj narzędzie
                if tool_name == "get_meta_ads_data":
                    tool_result = meta_ads_tool(
                        date_from=tool_input.get('date_from'),
                        date_to=tool_input.get('date_to'),
                        campaign_name=tool_input.get('campaign_name'),
                        metrics=tool_input.get('metrics')
                    )
                else:
                    tool_result = {"error": "Nieznane narzędzie"}
                
                # Dodaj odpowiedź Claude'a do historii
                messages.append({"role": "assistant", "content": response.content})
                
                # Dodaj wynik narzędzia
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_block.id,
                            "content": str(tool_result)
                        }
                    ]
                })
                
                # Kontynuuj pętlę - Claude przeanalizuje wynik
                continue
                
            else:
                # Claude skończył - wyślij ostatnią odpowiedź
                response_text = next(
                    (block.text for block in response.content if hasattr(block, "text")),
                    "Przepraszam, nie mogłem wygenerować odpowiedzi."
                )
                say(text=response_text, thread_ts=thread_ts)
                break
        
    except Exception as e:
        logger.error(f"Błąd: {e}")
        say(text=f"Przepraszam, wystąpił błąd: {str(e)}", thread_ts=thread_ts)
    
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
# Reaguj na wzmianki (@bot)
@app.event("message")
def handle_message_events(body, say, logger):
    logger.info(body)
    event = body["event"]
    
    if event.get("channel_type") == "im" and event.get("user") in checkin_responses:
        user_message = event.get("text", "")
        checkin_responses[event["user"]].append(user_message)
        say("✅ Dziękuję za odpowiedź! Twój feedback jest dla nas ważny. 🙏")
        return
    
    if event.get("bot_id"):
        return
    
    if event.get("subtype") == "bot_message":
        return
    
    user_message = event.get("text", "")
    user_id = event.get("user")
    
    try:
        history = get_conversation_history(user_id)
        save_message_to_history(user_id, "user", user_message)
        
        message = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=get_conversation_history(user_id)
        )
        
        response_text = message.content[0].text
        save_message_to_history(user_id, "assistant", response_text)
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
                if len(messages) >= 3:
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
scheduler.add_job(daily_summaries, 'cron', hour=16, minute=0)
scheduler.start()


# Weekly check-in - piątek 16:00
def weekly_checkin():
    warsaw_tz = pytz.timezone('Europe/Warsaw')
    
    try:
        logger.info("🔥 ROZPOCZYNAM WEEKLY CHECK-IN!")  # <-- DODAJ TO
        
        # Pobierz listę wszystkich użytkowników
        result = app.client.users_list()
        users = result["members"]
        
        logger.info(f"📊 Znalazłem {len(users)} użytkowników")  # <-- I TO
        
        for user in users:
            # Pomiń boty i deactivated users
            if user.get("is_bot") or user.get("deleted"):
                continue
                
            user_id = user["id"]
            logger.info(f"✉️ Wysyłam do {user_id}")  # <-- I TO
            
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
        logger.error(f"Błąd podczas wysyłania check-inów: {e}")

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
scheduler.add_job(weekly_checkin, 'cron', day_of_week='fri', hour=14, minute=0)
scheduler.add_job(checkin_summary, 'cron', day_of_week='mon', hour=9, minute=0)
print(f"✅ Scheduler załadowany! Jobs: {len(scheduler.get_jobs())}")
print("✅ Scheduler wystartował!")
# Uruchom bota
handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
print("⚡️ Bot działa!")
handler.start()
