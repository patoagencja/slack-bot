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
import json
from imapclient import IMAPClient
import email
from email.header import decode_header
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.ads.googleads.client import GoogleAdsClient

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
# Inicjalizacja Google Ads API
try:
    google_ads_config = {
        'developer_token': os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN"),
        'client_id': os.environ.get("GOOGLE_ADS_CLIENT_ID"),
        'client_secret': os.environ.get("GOOGLE_ADS_CLIENT_SECRET"),
        'refresh_token': os.environ.get("GOOGLE_ADS_REFRESH_TOKEN"),
        'login_customer_id': '6878731454',
        'use_proto_plus': True
}
    google_ads_client = GoogleAdsClient.load_from_dict(google_ads_config)
    logger.info("✅ Google Ads API zainicjalizowane")
except Exception as e:
    logger.error(f"Błąd inicjalizacji Google Ads API: {e}")
    google_ads_client = None
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

def parse_relative_date(date_string):
    """Konwertuj względne daty na YYYY-MM-DD"""
    from datetime import datetime, timedelta
    
    if not date_string:
        return None
    
    # Już jest w formacie YYYY-MM-DD
    if len(date_string) == 10 and date_string[4] == '-' and date_string[7] == '-':
        return date_string
    
    today = datetime.now()
    
    # Parsuj względne daty
    date_lower = date_string.lower()
    
    # Dzisiaj
    if 'dzisiaj' in date_lower or 'today' in date_lower:
        return today.strftime('%Y-%m-%d')
    
    if 'wczoraj' in date_lower or 'yesterday' in date_lower:
        return (today - timedelta(days=1)).strftime('%Y-%m-%d')
    elif 'tydzień' in date_lower or 'week' in date_lower:
        days = 7
        if 'ostatni' in date_lower or 'last' in date_lower:
            return (today - timedelta(days=days)).strftime('%Y-%m-%d')
    elif 'miesiąc' in date_lower or 'month' in date_lower:
        return (today - timedelta(days=30)).strftime('%Y-%m-%d')
    
    # Spróbuj wyciągnąć liczbę dni
    import re
    match = re.search(r'(\d+)\s*(dzień|dni|day|days)', date_lower)
    if match:
        days = int(match.group(1))
        return (today - timedelta(days=days)).strftime('%Y-%m-%d')
    
    # Jeśli nic nie pasuje, zwróć oryginalny string
    return date_string

# Narzędzie Meta Ads dla Claude - ROZSZERZONE Z MULTI-ACCOUNT
def meta_ads_tool(date_from=None, date_to=None, level="campaign", campaign_name=None, adset_name=None, ad_name=None, metrics=None, breakdown=None, limit=None, client_name=None):
    """
    Pobiera dane z Meta Ads API na różnych poziomach dla różnych klientów.
    
    Args:
        date_from: Data początkowa YYYY-MM-DD (domyślnie wczoraj)
        date_to: Data końcowa YYYY-MM-DD (domyślnie dzisiaj)
        level: Poziom danych - "campaign", "adset", "ad" (domyślnie "campaign")
        campaign_name: Filtr po nazwie kampanii (opcjonalne)
        adset_name: Filtr po nazwie ad setu (opcjonalne)
        ad_name: Filtr po nazwie reklamy (opcjonalne)
        metrics: Lista metryk do pobrania (opcjonalne)
        breakdown: Breakdown dla insights (opcjonalne)
        limit: Limit wyników (opcjonalne)
        client_name: Nazwa klienta/biznesu (opcjonalne - jeśli nie podano, zwraca listę)
    
    Returns:
        JSON ze statystykami
    """
    # Wczytaj mapowanie kont reklamowych
    accounts_json = os.environ.get("META_AD_ACCOUNTS", "{}")
    try:
        accounts_map = json.loads(accounts_json)
    except json.JSONDecodeError:
        accounts_map = {}
    
    # Jeśli nie podano klienta - zwróć listę dostępnych
    if not client_name:
        available_clients = list(set(accounts_map.keys()))
        return {
            "message": "Nie podano nazwy klienta. Dostępne klienty:",
            "available_clients": available_clients,
            "hint": "Podaj nazwę klienta w zapytaniu, np. 'jak wypadły kampanie dla instax?'"
        }
    
    # Znajdź Account ID dla klienta (case-insensitive)
    client_name_lower = client_name.lower()
    ad_account_id = None
    
    for key, value in accounts_map.items():
        if key.lower() == client_name_lower or client_name_lower in key.lower():
            ad_account_id = value
            break
    
    if not ad_account_id:
        return {
            "error": f"Nie znaleziono konta dla klienta '{client_name}'",
            "available_clients": list(set(accounts_map.keys())),
            "hint": "Sprawdź pisownię lub wybierz z dostępnych klientów"
        }
    
    try:
        # Konwertuj względne daty
        if date_from:
            date_from = parse_relative_date(date_from)
        if date_to:
            date_to = parse_relative_date(date_to)
        
        # Walidacja roku - napraw daty z przeszłości
        if date_from and len(date_from) >= 4:
            year = int(date_from[:4])
            if year < 2026:
                date_from = '2026' + date_from[4:]
        
        if date_to and len(date_to) >= 4:
            year = int(date_to[:4])
            if year < 2026:
                date_to = '2026' + date_to[4:]
        
        # Domyślne daty
        if not date_to:
            date_to = datetime.now().strftime('%Y-%m-%d')
        if not date_from:
            date_from = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        account = AdAccount(ad_account_id)
        
        # Wszystkie dostępne metryki
        available_metrics = {
            'campaign': ['campaign_name', 'spend', 'impressions', 'clicks', 'ctr', 'cpc', 'cpm', 'reach', 'frequency', 
                        'conversions', 'cost_per_conversion', 'purchase_roas', 'actions', 'action_values'],
            'adset': ['campaign_name', 'adset_name', 'spend', 'impressions', 'clicks', 'ctr', 'cpc', 'cpm', 'reach',
                     'conversions', 'cost_per_conversion', 'budget_remaining', 'budget_rebalance_flag'],
            'ad': ['campaign_name', 'adset_name', 'ad_name', 'spend', 'impressions', 'clicks', 'ctr', 'cpc', 'cpm',
                  'reach', 'conversions', 'inline_link_clicks', 'inline_link_click_ctr']
        }
        
        # Użyj podanych metryk lub domyślnych
        if not metrics:
            metrics = available_metrics.get(level, available_metrics['campaign'])
        
        # Parametry insights
        params = {
            'time_range': {'since': date_from, 'until': date_to},
            'level': level,
            'fields': metrics
        }
        
        # Dodaj breakdown jeśli podano
        if breakdown:
            params['breakdowns'] = [breakdown] if isinstance(breakdown, str) else breakdown
        
        # Dodaj limit jeśli podano
        if limit:
            params['limit'] = limit
        
        # Pobierz insights
        insights = account.get_insights(params=params)
        
        if not insights:
            return {"message": f"Brak danych za okres {date_from} - {date_to} na poziomie {level}"}
        
        # Konwertuj do listy słowników
        data = []
        for insight in insights:
            item = {}
            
            # Podstawowe pola
            for metric in metrics:
                value = insight.get(metric)
                if value is not None:
                    # Konwersja typów
                    if metric in ['spend', 'cpc', 'cpm', 'ctr', 'frequency', 'cost_per_conversion', 'purchase_roas', 
                                 'budget_remaining', 'inline_link_click_ctr']:
                        item[metric] = float(value)
                    elif metric in ['impressions', 'clicks', 'reach', 'conversions', 'inline_link_clicks']:
                        item[metric] = int(value)
                    elif metric in ['actions', 'action_values']:
                        # Te są jako listy obiektów - zostaw jako są
                        item[metric] = value
                    else:
                        item[metric] = str(value)
            
            # Breakdown fields (age, gender, placement, etc.)
            if breakdown:
                breakdown_list = [breakdown] if isinstance(breakdown, str) else breakdown
                for b in breakdown_list:
                    if b in insight:
                        item[b] = insight[b]
            
            # Filtrowanie
            should_include = True
            
            if campaign_name and 'campaign_name' in item:
                if campaign_name.lower() not in item['campaign_name'].lower():
                    should_include = False
            
            if adset_name and 'adset_name' in item:
                if adset_name.lower() not in item['adset_name'].lower():
                    should_include = False
            
            if ad_name and 'ad_name' in item:
                if ad_name.lower() not in item['ad_name'].lower():
                    should_include = False
            
            if should_include:
                data.append(item)
        
        return {
            "date_from": date_from,
            "date_to": date_to,
            "level": level,
            "breakdown": breakdown,
            "total_items": len(data),
            "data": data
        }
        
    except Exception as e:
        logger.error(f"Błąd pobierania danych Meta Ads: {e}")
        return {"error": str(e)}
# Narzędzie Google Ads dla Claude - MULTI-ACCOUNT
def google_ads_tool(date_from=None, date_to=None, level="campaign", campaign_name=None, adgroup_name=None, ad_name=None, metrics=None, limit=None, client_name=None):
    """
    Pobiera dane z Google Ads API na różnych poziomach dla różnych klientów.
    
    Args:
        date_from: Data początkowa YYYY-MM-DD
        date_to: Data końcowa YYYY-MM-DD
        level: Poziom danych - "campaign", "adgroup", "ad"
        campaign_name: Filtr po nazwie kampanii
        adgroup_name: Filtr po nazwie ad group
        ad_name: Filtr po nazwie reklamy
        metrics: Lista metryk do pobrania
        limit: Limit wyników
        client_name: Nazwa klienta/biznesu
    
    Returns:
        JSON ze statystykami
    """
    if not google_ads_client:
        return {"error": "Google Ads API nie jest skonfigurowane."}
    
    # Wczytaj mapowanie kont
    accounts_json = os.environ.get("GOOGLE_ADS_CUSTOMER_IDS", "{}")
    try:
        accounts_map = json.loads(accounts_json)
    except json.JSONDecodeError:
        accounts_map = {}
    
    # Jeśli nie podano klienta - zwróć listę
    if not client_name:
        available_clients = list(set(accounts_map.keys()))
        return {
            "message": "Nie podano nazwy klienta. Dostępne klienty:",
            "available_clients": available_clients,
            "hint": "Podaj nazwę klienta w zapytaniu"
        }
    
    # Znajdź Customer ID
    client_name_lower = client_name.lower()
    customer_id = None
    
    for key, value in accounts_map.items():
        if key.lower() == client_name_lower or client_name_lower in key.lower():
            customer_id = value
            break
    
    if not customer_id:
        return {
            "error": f"Nie znaleziono konta dla klienta '{client_name}'",
            "available_clients": list(set(accounts_map.keys())),
            "hint": "Sprawdź pisownię"
        }
    
    try:
        # Konwertuj daty
        if date_from:
            date_from = parse_relative_date(date_from)
        if date_to:
            date_to = parse_relative_date(date_to)
        
        # Walidacja roku
        if date_from and len(date_from) >= 4:
            year = int(date_from[:4])
            if year < 2026:
                date_from = '2026' + date_from[4:]
        
        if date_to and len(date_to) >= 4:
            year = int(date_to[:4])
            if year < 2026:
                date_to = '2026' + date_to[4:]
        
        # Domyślne daty
        if not date_to:
            date_to = datetime.now().strftime('%Y-%m-%d')
        if not date_from:
            date_from = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        
        # Formatuj daty dla Google Ads (YYYYMMDD)
        date_from_ga = date_from.replace('-', '')
        date_to_ga = date_to.replace('-', '')
        
        # Metryki domyślne
        default_metrics = {
            'campaign': ['campaign.name', 'metrics.impressions', 'metrics.clicks', 'metrics.cost_micros', 
                        'metrics.conversions', 'metrics.ctr', 'metrics.average_cpc'],
            'adgroup': ['campaign.name', 'ad_group.name', 'metrics.impressions', 'metrics.clicks', 
                       'metrics.cost_micros', 'metrics.conversions', 'metrics.ctr'],
            'ad': ['campaign.name', 'ad_group.name', 'ad_group_ad.ad.name', 'metrics.impressions', 
                  'metrics.clicks', 'metrics.cost_micros', 'metrics.ctr']
        }
        
        if not metrics:
            metrics = default_metrics.get(level, default_metrics['campaign'])
        
        # Zbuduj zapytanie GAQL
        resource_map = {
            'campaign': 'campaign',
            'adgroup': 'ad_group',
            'ad': 'ad_group_ad'
        }
        
        resource = resource_map.get(level, 'campaign')
        fields = ', '.join(metrics)
        
        query = f"""
            SELECT {fields}
            FROM {resource}
            WHERE segments.date BETWEEN '{date_from_ga}' AND '{date_to_ga}'
        """
        
        # Dodaj filtry
        if campaign_name:
            query += f" AND campaign.name LIKE '%{campaign_name}%'"
        if adgroup_name and level in ['adgroup', 'ad']:
            query += f" AND ad_group.name LIKE '%{adgroup_name}%'"
        
        if limit:
            query += f" LIMIT {limit}"
        
        # Wykonaj zapytanie
        ga_service = google_ads_client.get_service("GoogleAdsService")
        response = ga_service.search(customer_id=customer_id, query=query)
        
        # Przetwórz wyniki
        data = []
        for row in response:
            item = {}
            
            # Wyciągnij wartości z różnych poziomów
            for metric in metrics:
                parts = metric.split('.')
                value = row
                
                try:
                    for part in parts:
                        value = getattr(value, part)
                    
                    # Konwertuj cost_micros na walutę
                    if 'cost_micros' in metric:
                        item['cost'] = float(value) / 1000000
                    elif 'ctr' in metric or 'cpc' in metric:
                        item[parts[-1]] = float(value)
                    elif isinstance(value, (int, float)):
                        item[parts[-1]] = value
                    else:
                        item[parts[-1]] = str(value)
                except:
                    pass
            
            # Filtrowanie
            should_include = True
            
            if campaign_name and 'name' in item:
                if campaign_name.lower() not in str(item.get('name', '')).lower():
                    should_include = False
            
            if should_include:
                data.append(item)
        
        return {
            "date_from": date_from,
            "date_to": date_to,
            "level": level,
            "customer_id": customer_id,
            "total_items": len(data),
            "data": data
        }
        
    except Exception as e:
        logger.error(f"Błąd pobierania danych Google Ads: {e}")
        return {"error": str(e)}

# Narzędzia Slack dla Claude
def slack_read_channel_tool(channel_id, limit=50, oldest=None, latest=None):
    """Czyta historię wiadomości z kanału"""
    try:
        # Konwertuj daty na timestampy jeśli podano
        params = {
            'channel': channel_id,
            'limit': min(limit, 100)
        }
        
        if oldest:
            # Jeśli to data YYYY-MM-DD, konwertuj na timestamp
            if len(oldest) == 10:
                dt = datetime.strptime(oldest, '%Y-%m-%d')
                params['oldest'] = str(int(dt.timestamp()))
            else:
                params['oldest'] = oldest
        
        if latest:
            if len(latest) == 10:
                dt = datetime.strptime(latest, '%Y-%m-%d')
                params['latest'] = str(int(dt.timestamp()))
            else:
                params['latest'] = latest
        
        result = app.client.conversations_history(**params)
        messages = result.get('messages', [])
        
        # Formatuj wiadomości
        formatted = []
        for msg in messages:
            user_id = msg.get('user', 'Unknown')
            text = msg.get('text', '')
            ts = msg.get('ts', '')
            
            # Konwertuj timestamp na czytelną datę
            if ts:
                dt = datetime.fromtimestamp(float(ts))
                date_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                date_str = 'Unknown'
            
            formatted.append({
                'user': user_id,
                'text': text,
                'timestamp': ts,
                'date': date_str,
                'has_thread': msg.get('reply_count', 0) > 0,
                'thread_ts': msg.get('thread_ts')
            })
        
        return {
            'channel_id': channel_id,
            'message_count': len(formatted),
            'messages': formatted
        }
        
    except Exception as e:
        logger.error(f"Błąd czytania kanału: {e}")
        return {"error": str(e)}

def slack_search_tool(query, sort='timestamp', limit=20):
    """Wyszukuje wiadomości na Slacku"""
    try:
        result = app.client.search_messages(
            query=query,
            sort=sort,
            count=min(limit, 100)
        )
        
        matches = result.get('messages', {}).get('matches', [])
        
        formatted = []
        for match in matches:
            formatted.append({
                'user': match.get('username', 'Unknown'),
                'text': match.get('text', ''),
                'channel': match.get('channel', {}).get('name', 'Unknown'),
                'timestamp': match.get('ts', ''),
                'permalink': match.get('permalink', '')
            })
        
        return {
            'query': query,
            'result_count': len(formatted),
            'results': formatted
        }
        
    except Exception as e:
        logger.error(f"Błąd wyszukiwania: {e}")
        return {"error": str(e)}

def slack_read_thread_tool(channel_id, thread_ts):
    """Czyta wątek (thread) z kanału"""
    try:
        result = app.client.conversations_replies(
            channel=channel_id,
            ts=thread_ts
        )
        
        messages = result.get('messages', [])
        
        formatted = []
        for msg in messages:
            user_id = msg.get('user', 'Unknown')
            text = msg.get('text', '')
            ts = msg.get('ts', '')
            
            if ts:
                dt = datetime.fromtimestamp(float(ts))
                date_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                date_str = 'Unknown'
            
            formatted.append({
                'user': user_id,
                'text': text,
                'timestamp': ts,
                'date': date_str
            })
        
        return {
            'channel_id': channel_id,
            'thread_ts': thread_ts,
            'reply_count': len(formatted) - 1,  # -1 bo pierwsza to parent message
            'messages': formatted
        }
        
    except Exception as e:
        logger.error(f"Błąd czytania wątku: {e}")
        return {"error": str(e)}
# Funkcja pomocnicza do pobierania danych email użytkownika
def get_user_email_config(user_id):
    """Pobierz konfigurację email dla danego użytkownika"""
    email_accounts_json = os.environ.get("EMAIL_ACCOUNTS", "{}")
    try:
        email_accounts = json.loads(email_accounts_json)
        return email_accounts.get(user_id)
    except json.JSONDecodeError:
        logger.error("Błąd parsowania EMAIL_ACCOUNTS")
        return None

# Narzędzie Email dla Claude
def email_tool(user_id, action, **kwargs):
    """
    Zarządza emailami użytkownika.
    
    Args:
        user_id: ID użytkownika Slack
        action: 'read' | 'send' | 'search'
        **kwargs: Parametry zależne od akcji
    
    Returns:
        JSON z wynikami
    """
    # Pobierz dane email użytkownika
    email_config = get_user_email_config(user_id)
    
    if not email_config:
        return {"error": "Nie masz skonfigurowanego konta email. Skontaktuj się z administratorem."}
    
    try:
        if action == "read":
            return read_emails(email_config, kwargs.get('limit', 10), kwargs.get('folder', 'INBOX'))
        elif action == "send":
            return send_email(email_config, kwargs.get('to'), kwargs.get('subject'), kwargs.get('body'))
        elif action == "search":
            return search_emails(email_config, kwargs.get('query'), kwargs.get('limit', 10))
        else:
            return {"error": f"Nieznana akcja: {action}"}
    except Exception as e:
        logger.error(f"Błąd email tool: {e}")
        return {"error": str(e)}

def read_emails(config, limit=10, folder='INBOX'):
    """Odczytaj najnowsze emaile"""
    try:
        with IMAPClient(config['imap_server'], ssl=True, port=993) as client:
            client.login(config['email'], config['password'])
            client.select_folder(folder)
            
            # Pobierz najnowsze emaile
            messages = client.search(['ALL'])
            messages = messages[-limit:] if len(messages) > limit else messages
            
            emails_data = []
            for uid in reversed(messages):
                raw_message = client.fetch([uid], ['RFC822'])[uid][b'RFC822']
                msg = email.message_from_bytes(raw_message)
                
                # Dekoduj subject
                subject = decode_header(msg['Subject'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
                
                # Pobierz treść
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode()
                            break
                else:
                    body = msg.get_payload(decode=True).decode()
                
                emails_data.append({
                    "from": msg['From'],
                    "subject": subject,
                    "date": msg['Date'],
                    "body_preview": body[:200] + "..." if len(body) > 200 else body
                })
            
            return {
                "folder": folder,
                "count": len(emails_data),
                "emails": emails_data
            }
    
    except Exception as e:
        return {"error": f"Błąd odczytu emaili: {str(e)}"}

def send_email(config, to, subject, body):
    """Wyślij email"""
    try:
        # Dodaj stopkę jeśli istnieje
        signature = os.environ.get("EMAIL_SIGNATURE", "")
        if signature:
            body = f"{body}\n\n{signature}"
        
        msg = MIMEMultipart()
        msg['From'] = config['email']
        msg['To'] = to
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        with smtplib.SMTP_SSL(config['smtp_server'], 465) as server:
            server.login(config['email'], config['password'])
            server.send_message(msg)
        
        return {
            "success": True,
            "message": f"Email wysłany do {to}",
            "subject": subject
        }
    except Exception as e:
        return {"error": f"Błąd wysyłania emaila: {str(e)}"}

def search_emails(config, query, limit=10):
    """Szukaj emaili po frazie"""
    try:
        with IMAPClient(config['imap_server'], ssl=True, port=993) as client:
            client.login(config['email'], config['password'])
            client.select_folder('INBOX')
            
            # Szukaj w subject i body
            messages = client.search(['OR', 'SUBJECT', query, 'BODY', query])
            messages = messages[-limit:] if len(messages) > limit else messages
            
            emails_data = []
            for uid in reversed(messages):
                raw_message = client.fetch([uid], ['RFC822'])[uid][b'RFC822']
                msg = email.message_from_bytes(raw_message)
                
                subject = decode_header(msg['Subject'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
                
                emails_data.append({
                    "from": msg['From'],
                    "subject": subject,
                    "date": msg['Date']
                })
            
            return {
                "query": query,
                "count": len(emails_data),
                "emails": emails_data
            }
    except Exception as e:
        return {"error": f"Błąd wyszukiwania: {str(e)}"}

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
            "description": "Pobiera szczegółowe statystyki z Meta Ads (Facebook Ads) na poziomie kampanii, ad setów lub pojedynczych reklam. Obsługuje breakdowny demograficzne i placement. Użyj gdy użytkownik pyta o kampanie, ad sety, reklamy, wydatki, wyniki, konwersje, ROAS, demografię (wiek/płeć/kraj) lub placement (Instagram/Facebook/Stories).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "client_name": {
                        "type": "string",
                        "description": "Nazwa klienta/biznesu. WYMAGANE. Dostępne: 'instax', 'fuji', 'instax/fuji', 'zbiorcze', 'kampanie zbiorcze', 'drzwi dre'. Wyciągnij z pytania użytkownika (np. 'jak kampanie dla instax?' → client_name='instax'). Jeśli użytkownik nie poda - zapytaj."
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Data początkowa. Format: YYYY-MM-DD lub względnie ('wczoraj', 'ostatni tydzień', 'ostatni miesiąc', '7 dni temu')."
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Data końcowa. Format: YYYY-MM-DD lub 'dzisiaj'. Domyślnie dzisiaj."
                    },
                    "level": {
                        "type": "string",
                        "enum": ["campaign", "adset", "ad"],
                        "description": "Poziom danych: 'campaign' (kampanie), 'adset' (zestawy reklam), 'ad' (pojedyncze reklamy). Domyślnie 'campaign'."
                    },
                    "campaign_name": {
                        "type": "string",
                        "description": "Filtr po nazwie kampanii (częściowa nazwa działa)."
                    },
                    "adset_name": {
                        "type": "string",
                        "description": "Filtr po nazwie ad setu (częściowa nazwa działa)."
                    },
                    "ad_name": {
                        "type": "string",
                        "description": "Filtr po nazwie reklamy (częściowa nazwa działa)."
                    },
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista metryk: campaign_name, adset_name, ad_name, spend, impressions, clicks, ctr, cpc, cpm, reach, frequency, conversions, cost_per_conversion, purchase_roas, actions, action_values, budget_remaining, inline_link_clicks, inline_link_click_ctr"
                    },
                    "breakdown": {
                        "type": "string",
                        "description": "Breakdown dla demografii/placement: 'age' (wiek), 'gender' (płeć), 'country' (kraj), 'placement' (miejsce wyświetlenia), 'device_platform' (urządzenie). Może być też lista np. ['age', 'gender']"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Limit wyników (max liczba kampanii/adsetów/reklam do zwrócenia)."
                    }
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
                    "action": {
                        "type": "string",
                        "enum": ["read", "send", "search"],
                        "description": "Akcja: 'read' = odczytaj najnowsze emaile, 'send' = wyślij email, 'search' = szukaj emaili po frazie"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Ile emaili pobrać/przeszukać (domyślnie 10)"
                    },
                    "to": {
                        "type": "string",
                        "description": "Adres odbiorcy (tylko dla action='send')"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Temat emaila (tylko dla action='send')"
                    },
                    "body": {
                        "type": "string",
                        "description": "Treść emaila (tylko dla action='send')"
                    },
                    "query": {
                        "type": "string",
                        "description": "Fraza do wyszukania (tylko dla action='search')"
                    }
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
                    "client_name": {
                        "type": "string",
                        "description": "Nazwa klienta/biznesu. WYMAGANE. Dostępne: '3wm', 'pato', 'dre 2024', 'dre24', 'dre 2025', 'dre25', 'dre', 'm2', 'zbiorcze'. Wyciągnij z pytania użytkownika."
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Data początkowa. Format: YYYY-MM-DD lub względnie ('wczoraj', 'ostatni tydzień')."
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Data końcowa. Format: YYYY-MM-DD lub 'dzisiaj'. Domyślnie dzisiaj."
                    },
                    "level": {
                        "type": "string",
                        "enum": ["campaign", "adgroup", "ad"],
                        "description": "Poziom danych: 'campaign' (kampanie), 'adgroup' (grupy reklam), 'ad' (pojedyncze reklamy). Domyślnie 'campaign'."
                    },
                    "campaign_name": {
                        "type": "string",
                        "description": "Filtr po nazwie kampanii."
                    },
                    "adgroup_name": {
                        "type": "string",
                        "description": "Filtr po nazwie ad group."
                    },
                    "ad_name": {
                        "type": "string",
                        "description": "Filtr po nazwie reklamy."
                    },
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista metryk: campaign.name, ad_group.name, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.ctr, metrics.average_cpc"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Limit wyników."
                    }
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
                    "channel_id": {
                        "type": "string",
                        "description": "ID kanału Slack. Jeśli użytkownik mówi 'ten kanał' lub 'tutaj', zostaw PUSTE - bot użyje obecnego kanału automatycznie."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Ile wiadomości pobrać (domyślnie 50, max 100)"
                    },
                    "oldest": {
                        "type": "string",
                        "description": "Data/timestamp od której czytać (format: YYYY-MM-DD lub Unix timestamp)"
                    },
                    "latest": {
                        "type": "string",
                        "description": "Data/timestamp do której czytać (format: YYYY-MM-DD lub Unix timestamp)"
                    }
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
                    "channel_id": {
                        "type": "string",
                        "description": "ID kanału"
                    },
                    "thread_ts": {
                        "type": "string",
                        "description": "Timestamp wiadomości która rozpoczyna wątek"
                    }
                },
                "required": ["channel_id", "thread_ts"]
            }
        }
    ]
    
    try:
        # Pobierz User ID
        user_id = event.get('user')
        
        # Pobierz historię konwersacji użytkownika (bez zapisywania jeszcze)
        history = get_conversation_history(user_id)

        # Stwórz messages dla tego zapytania (bez modyfikowania globalnej historii)
        messages = history + [{"role": "user", "content": user_message}]
        
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
                    user_id = event.get('user')
                    tool_result = email_tool(
                        user_id=user_id,
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
                    channel_id = tool_input.get('channel_id') or event.get('channel')
                    tool_result = slack_read_channel_tool(
                        channel_id=channel_id,
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
                
                # Zapisz całą konwersację do historii (user + assistant)
                save_message_to_history(user_id, "user", user_message)
                save_message_to_history(user_id, "assistant", response_text)
                
                say(text=response_text, thread_ts=thread_ts)
                break
        
    except Exception as e:
        logger.error(f"Błąd: {e}")
        say(text=f"Przepraszam, wystąpił błąd: {str(e)}", thread_ts=thread_ts)


# Reaguj na wiadomości DM
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
                
                # Tylko jeśli jest 3+ wiadomości
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

# Weekly check-in - piątek 14:00
def weekly_checkin():
    warsaw_tz = pytz.timezone('Europe/Warsaw')
    
    try:
        logger.info("🔥 ROZPOCZYNAM WEEKLY CHECK-IN!")
        
        # Pobierz listę wszystkich użytkowników
        result = app.client.users_list()
        users = result["members"]
        
        logger.info(f"📊 Znalazłem {len(users)} użytkowników")
        
        for user in users:
            # Pomiń boty i deactivated users
            if user.get("is_bot") or user.get("deleted"):
                continue
                
            user_id = user["id"]
            logger.info(f"✉️ Wysyłam do {user_id}")
            
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
        YOUR_USER_ID = "UTE1RN6SJ"
        
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

# Scheduler - codziennie o 16:00
scheduler = BackgroundScheduler(timezone=pytz.timezone('Europe/Warsaw'))
scheduler.add_job(daily_summaries, 'cron', hour=16, minute=0)
scheduler.add_job(weekly_checkin, 'cron', day_of_week='fri', hour=14, minute=0)
scheduler.add_job(checkin_summary, 'cron', day_of_week='mon', hour=9, minute=0)
scheduler.start()

print(f"✅ Scheduler załadowany! Jobs: {len(scheduler.get_jobs())}")
print("✅ Scheduler wystartował!")

# Uruchom bota
handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
print("⚡️ Bot działa!")
handler.start()
