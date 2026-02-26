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
        'login_customer_id': os.environ.get('GOOGLE_ADS_LOGIN_CUSTOMER_ID', '1355353554'),
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
        # Parsuj nazwy miesięcy (np. "grudzień 2025", "styczeń 2026")
    import re
    from datetime import datetime
    
    # Lista miesięcy po polsku
    months = {
        'styczeń': 1, 'stycznia': 1,
        'luty': 2, 'lutego': 2,
        'marzec': 3, 'marca': 3,
        'kwiecień': 4, 'kwietnia': 4,
        'maj': 5, 'maja': 5,
        'czerwiec': 6, 'czerwca': 6,
        'lipiec': 7, 'lipca': 7,
        'sierpień': 8, 'sierpnia': 8,
        'wrzesień': 9, 'września': 9,
        'październik': 10, 'października': 10,
        'listopad': 11, 'listopada': 11,
        'grudzień': 12, 'grudnia': 12
    }
    
    # Spróbuj match "miesiąc YYYY" (np. "grudzień 2025")
    for month_name, month_num in months.items():
        if month_name in date_lower:
            # Szukaj roku
            year_match = re.search(r'202[0-9]', date_string)
            if year_match:
                year = int(year_match.group())
                # Pierwszy dzień miesiąca
                date_obj = datetime(year, month_num, 1)
                return date_obj.strftime('%Y-%m-%d')
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
                
                # Dekoduj subject (obsługa różnych encodingów)
                subject_parts = []
                for part, charset in decode_header(msg['Subject'] or ''):
                    if isinstance(part, bytes):
                        subject_parts.append(part.decode(charset or 'utf-8', errors='replace'))
                    else:
                        subject_parts.append(part or '')
                subject = ''.join(subject_parts)

                # Dekoduj From (może mieć encoded words)
                sender_parts = []
                for part, charset in decode_header(msg['From'] or ''):
                    if isinstance(part, bytes):
                        sender_parts.append(part.decode(charset or 'utf-8', errors='replace'))
                    else:
                        sender_parts.append(part or '')
                sender = ''.join(sender_parts)

                # Pobierz treść z wykrywaniem kodowania
                def _decode_payload(part_or_msg):
                    raw = part_or_msg.get_payload(decode=True)
                    if not raw:
                        return ""
                    charset = part_or_msg.get_content_charset()
                    for enc in [charset, 'utf-8', 'latin-1', 'cp1250', 'iso-8859-2']:
                        if not enc:
                            continue
                        try:
                            return raw.decode(enc)
                        except (UnicodeDecodeError, LookupError):
                            continue
                    return raw.decode('utf-8', errors='replace')

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = _decode_payload(part)
                            break
                else:
                    body = _decode_payload(msg)
                
                # Wykryj czy to newsletter/mailing (przed dodaniem)
                is_newsletter = bool(
                    msg.get('List-Unsubscribe') or
                    msg.get('List-Id') or
                    msg.get('X-Mailchimp-ID') or
                    msg.get('X-Campaign') or
                    (msg.get('Precedence', '').lower() in ['bulk', 'list', 'junk'])
                )

                emails_data.append({
                    "from": sender,
                    "subject": subject,
                    "date": msg['Date'],
                    "body_preview": body[:200] + "..." if len(body) > 200 else body,
                    "is_newsletter": is_newsletter,
                })
            
            return {
                "folder": folder,
                "count": len(emails_data),
                "emails": emails_data
            }
    
    except Exception as e:
        return {"error": f"Błąd odczytu emaili: {str(e)}"}

def _normalize_subject(subject):
    """Usuwa prefixes Re:/Fwd:/Odp: i whitespace żeby porównać wątki."""
    import re as _re
    subject = subject or ""
    subject = _re.sub(r'^(Re|Fwd|FW|Odp|ODP|AW|SV|VS)(\s*\[\d+\])?:\s*', '', subject, flags=_re.IGNORECASE).strip()
    return subject.lower()


def find_unreplied_emails(config, received_emails, days_back=3):
    """
    Sprawdza które z podanych emaili nie mają odpowiedzi w folderze SENT.

    Args:
        config: konfiguracja IMAP
        received_emails: lista emaili (dict z 'subject', 'from', 'date')
        days_back: ile dni wstecz szukać w SENT (domyślnie 3)

    Returns:
        lista emaili bez odpowiedzi (te same dicty z dodanym 'days_waiting')
    """
    # Możliwe nazwy folderu SENT w różnych providerach
    SENT_FOLDERS = [
        "Sent", "SENT", "Sent Items", "Sent Messages",
        "[Gmail]/Sent Mail", "INBOX.Sent", "Poczta wysłana"
    ]

    try:
        with IMAPClient(config['imap_server'], ssl=True, port=993) as client:
            client.login(config['email'], config['password'])

            # Znajdź folder SENT
            sent_folder = None
            for folder in SENT_FOLDERS:
                try:
                    client.select_folder(folder, readonly=True)
                    sent_folder = folder
                    break
                except Exception:
                    continue

            if not sent_folder:
                logger.warning("Nie znaleziono folderu SENT — pomijam sprawdzanie odpowiedzi")
                return []

            # Pobierz wysłane z ostatnich days_back dni
            since_date = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')
            sent_uids = client.search(['SINCE', since_date])

            sent_subjects = set()
            for uid in sent_uids:
                try:
                    raw = client.fetch([uid], ['RFC822.HEADER'])[uid][b'RFC822.HEADER']
                    sent_msg = email.message_from_bytes(raw)
                    parts = decode_header(sent_msg.get('Subject', '') or '')
                    s_parts = []
                    for p, ch in parts:
                        if isinstance(p, bytes):
                            s_parts.append(p.decode(ch or 'utf-8', errors='replace'))
                        else:
                            s_parts.append(p or '')
                    sent_subjects.add(_normalize_subject(''.join(s_parts)))
                except Exception:
                    continue

            # Sprawdź które otrzymane emaile nie mają odpowiedzi
            unreplied = []
            for em in received_emails:
                normalized = _normalize_subject(em.get('subject', ''))
                # Odpowiedź istnieje jeśli w SENT jest email z tym samym tematem
                if normalized not in sent_subjects:
                    # Oblicz ile dni czeka bez odpowiedzi
                    days_waiting = 0
                    try:
                        from email.utils import parsedate_to_datetime
                        em_date = parsedate_to_datetime(em['date']).date()
                        days_waiting = (datetime.now().date() - em_date).days
                    except Exception:
                        pass
                    unreplied.append({**em, 'days_waiting': days_waiting})

            return unreplied

    except Exception as e:
        logger.error(f"Błąd find_unreplied_emails: {e}")
        return []


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

    msg_lower_m = user_message.lower()

    # === "zamknij #N" — Daniel zamyka prośbę ===
    import re as _re_m
    close_match = _re_m.search(r'zamknij\s+#?(\d+)', msg_lower_m)
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
        target_str = target.strftime('%Y-%m-%d')
        target_label = target.strftime('%A %d.%m.%Y')
        entries = get_availability_for_date(target_str)
        say(_format_availability_summary(entries, target_label))
        return

    # Email trigger - wyniki zawsze na DM, nie w kanale
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

    channel = event['channel']
    thread_ts = event.get('thread_ts', event['ts'])
    # Oblicz dzisiejszą datę dynamicznie
    from datetime import datetime
    today = datetime.now()
    today_formatted = today.strftime('%d %B %Y')
    today_iso = today.strftime('%Y-%m-%d')
        # ========================================
    # DODAJ TEN SYSTEM PROMPT TUTAJ:
    # ========================================
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
"""
    
    
    # ========================================
    # KONIEC SYSTEM PROMPT
    # ========================================
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
                system=SYSTEM_PROMPT,  # <-- DODAJ TĘ LINIĘ!
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

    # --- Manual triggers (obsługuj przed Claude) ---
    text_lower = user_message.lower()

    # Digest triggers - tylko w kanałach
    CHANNEL_CLIENT_MAP = {"C05GPM4E9B8": "dre"}
    if any(t in text_lower for t in ["digest test", "test digest", "digest", "raport"]):
        if event.get("channel_type") != "im":
            channel_id = event.get("channel")
            client_name = CHANNEL_CLIENT_MAP.get(channel_id)
            if client_name == "dre":
                say(generate_daily_digest_dre())
            else:
                say("Dla którego klienta? Dostępne: `dre` (wpisz np. `digest test dre`)")
            return

    # === AVAILABILITY: pracownik pisze o nieobecności (tylko DM) ===
    if event.get("channel_type") == "im":
        try:
            user_info = app.client.users_info(user=user_id)
            user_name = (user_info["user"].get("real_name")
                         or user_info["user"].get("profile", {}).get("display_name")
                         or user_info["user"].get("name", user_id))
        except Exception:
            user_name = user_id
        if handle_employee_dm(user_id, user_name, user_message, say):
            return

    # Email summary - trigger działa wszędzie, wyniki zawsze idą na DM
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
# ============================================
# DAILY DIGEST - ANOMALY DETECTION
# ============================================

def check_conversion_history(client_name, platform, campaign_name, lookback_days=30):
    """
    Sprawdza czy kampania kiedykolwiek miała conversions w historii.
    Używane do smart alerting - rozróżnienie między "coś się zepsuło" vs "to normalne".
    """
    try:
        date_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        date_to = datetime.now().strftime('%Y-%m-%d')
        
        if platform == "meta":
            data = meta_ads_tool(
                client_name=client_name,
                date_from=date_from,
                date_to=date_to,
                level="campaign",
                campaign_name=campaign_name,
                metrics=["campaign_name", "conversions"]
            )
            
            if data.get("data"):
                total_conversions = sum(item.get("conversions", 0) for item in data["data"])
                return {
                    "had_conversions": total_conversions > 0,
                    "total": total_conversions,
                    "alert_level": "CRITICAL" if total_conversions > 0 else "WARNING"
                }
        
        elif platform == "google":
            data = google_ads_tool(
                client_name=client_name,
                date_from=date_from,
                date_to=date_to,
                level="campaign",
                campaign_name=campaign_name,
                metrics=["campaign.name", "metrics.conversions"]
            )
            
            if data.get("data"):
                total_conversions = sum(item.get("conversions", 0) for item in data["data"])
                return {
                    "had_conversions": total_conversions > 0,
                    "total": total_conversions,
                    "alert_level": "CRITICAL" if total_conversions > 0 else "WARNING"
                }
        
        return {"had_conversions": False, "total": 0, "alert_level": "WARNING"}
        
    except Exception as e:
        logger.error(f"Błąd sprawdzania historii: {e}")
        return {"had_conversions": False, "total": 0, "alert_level": "WARNING"}


def analyze_campaign_trends(campaigns_data, lookback_days=7, goal="conversion"):
    """
    Analizuje trendy kampanii i wykrywa anomalie.
    goal: "conversion" (e-commerce, ROAS/konwersje) lub "engagement" (traffic, CTR/reach)
    Returns: dict z critical alerts, warnings, i top performers
    """
    critical_alerts = []
    warnings = []
    top_performers = []

    for campaign in campaigns_data:
        campaign_name = campaign.get("campaign_name") or campaign.get("name", "Unknown")
        conversions = campaign.get("conversions", 0)
        ctr = campaign.get("ctr", 0) or 0
        cpc = campaign.get("cpc") or campaign.get("average_cpc", 0) or 0
        spend = campaign.get("spend") or campaign.get("cost", 0) or 0
        roas = campaign.get("purchase_roas", 0) or 0
        frequency = campaign.get("frequency", 0) or 0
        reach = campaign.get("reach", 0) or 0
        impressions = campaign.get("impressions", 0) or 0

        if goal == "engagement":
            # === CEL: ENGAGEMENT / TRAFFIC ===
            # Nie alarmuj o zerowych konwersjach ani niskim ROAS — nie to mierzymy

            # CRITICAL: bardzo niski CTR (engagement powinien mieć CTR >0.8%)
            if ctr < 0.8 and spend > 50:
                critical_alerts.append({
                    "type": "low_ctr",
                    "campaign": campaign_name,
                    "ctr": ctr,
                    "message": f"CTR {ctr:.2f}% — bardzo niski dla kampanii engagement"
                })

            # WARNING: wysoka frequency (>4 = ad fatigue)
            if frequency > 4:
                warnings.append({
                    "type": "high_frequency",
                    "campaign": campaign_name,
                    "frequency": frequency,
                    "message": f"Frequency {frequency:.1f} — ryzyko ad fatigue (>4)"
                })

            # WARNING: frequency >2.5 (żółte ostrzeżenie wcześnie)
            elif frequency > 2.5:
                warnings.append({
                    "type": "medium_frequency",
                    "campaign": campaign_name,
                    "frequency": frequency,
                    "message": f"Frequency {frequency:.1f} — obserwuj (>2.5)"
                })

            # TOP PERFORMER: najlepszy CTR
            if ctr >= 1.5 and spend > 20:
                top_performers.append({
                    "campaign": campaign_name,
                    "ctr": ctr,
                    "cpc": cpc,
                    "spend": spend,
                    "reach": reach,
                    "impressions": impressions,
                })

        else:
            # === CEL: CONVERSION / E-COMMERCE ===

            # CRITICAL: Zero conversions przy dużym spendzie
            if conversions == 0 and spend > 50:
                critical_alerts.append({
                    "type": "zero_conversions",
                    "campaign": campaign_name,
                    "spend": spend,
                    "message": f"Zero conversions przy {spend:.2f} PLN wydatków"
                })

            # CRITICAL: Very low CTR
            if ctr < 0.5 and spend > 50:
                critical_alerts.append({
                    "type": "low_ctr",
                    "campaign": campaign_name,
                    "ctr": ctr,
                    "message": f"CTR {ctr:.2f}% (bardzo niski)"
                })

            # WARNING: Low ROAS
            if roas > 0 and roas < 2.0:
                warnings.append({
                    "type": "low_roas",
                    "campaign": campaign_name,
                    "roas": roas,
                    "message": f"ROAS {roas:.1f} (poniżej target 2.0)"
                })

            # TOP PERFORMER: Good ROAS
            if roas >= 3.5:
                top_performers.append({
                    "campaign": campaign_name,
                    "roas": roas,
                    "spend": spend,
                    "conversions": conversions
                })

    # Sort
    if goal == "engagement":
        top_performers.sort(key=lambda x: x.get("ctr", 0), reverse=True)
    else:
        top_performers.sort(key=lambda x: x.get("roas", 0), reverse=True)

    return {
        "critical_alerts": critical_alerts,
        "warnings": warnings,
        "top_performers": top_performers[:3],
        "goal": goal,
    }


def get_client_benchmarks(client_name, platform, lookback_days=30):
    """
    Pobiera benchmarki (30-dniowe średnie) dla klienta.

    Returns:
        dict z avg_ctr, avg_cpc, avg_roas, avg_frequency (lub None jeśli brak danych)
    """
    try:
        date_to = datetime.now().strftime('%Y-%m-%d')
        date_from = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        if platform == "meta":
            data = meta_ads_tool(
                client_name=client_name,
                date_from=date_from,
                date_to=date_to,
                level="campaign",
                metrics=["campaign_name", "spend", "impressions", "clicks", "ctr",
                         "cpc", "purchase_roas", "frequency", "conversions"]
            )
            campaigns = data.get("data", [])
            if not campaigns:
                return None

            ctrs = [c["ctr"] for c in campaigns if c.get("ctr")]
            cpcs = [c["cpc"] for c in campaigns if c.get("cpc")]
            roases = [c["purchase_roas"] for c in campaigns if c.get("purchase_roas")]
            freqs = [c["frequency"] for c in campaigns if c.get("frequency")]

            return {
                "avg_ctr": sum(ctrs) / len(ctrs) if ctrs else None,
                "avg_cpc": sum(cpcs) / len(cpcs) if cpcs else None,
                "avg_roas": sum(roases) / len(roases) if roases else None,
                "avg_frequency": sum(freqs) / len(freqs) if freqs else None,
                "period_days": lookback_days,
                "campaign_count": len(campaigns)
            }

        elif platform == "google":
            all_campaigns = []
            for account in ["dre", "dre 2024", "dre 2025"]:
                gdata = google_ads_tool(
                    client_name=account,
                    date_from=date_from,
                    date_to=date_to,
                    level="campaign",
                    metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                             "metrics.cost_micros", "metrics.conversions",
                             "metrics.ctr", "metrics.average_cpc"]
                )
                if gdata.get("data"):
                    all_campaigns.extend(gdata["data"])

            if not all_campaigns:
                return None

            ctrs = [c["ctr"] for c in all_campaigns if c.get("ctr")]
            cpcs = [c["cpc"] for c in all_campaigns if c.get("cpc")]

            return {
                "avg_ctr": sum(ctrs) / len(ctrs) if ctrs else None,
                "avg_cpc": sum(cpcs) / len(cpcs) if cpcs else None,
                "avg_roas": None,  # Google nie zwraca ROAS bezpośrednio
                "avg_frequency": None,
                "period_days": lookback_days,
                "campaign_count": len(all_campaigns)
            }

    except Exception as e:
        logger.error(f"Błąd pobierania benchmarków: {e}")
        return None


def _benchmark_flag(current, benchmark, higher_is_better=True):
    """
    Zwraca emoji + % różnicy vs benchmark.
    🔴 gorzej >20%, 🟡 gorzej 10-20%, ✅ ±10%, 🟢 lepiej >20%
    """
    if benchmark is None or benchmark == 0 or current is None:
        return ""
    diff_pct = (current - benchmark) / benchmark * 100
    if not higher_is_better:
        diff_pct = -diff_pct  # dla CPC niższy = lepszy

    if diff_pct >= 20:
        flag = "🟢"
    elif diff_pct >= 10:
        flag = "✅"
    elif diff_pct >= -10:
        flag = "✅"
    elif diff_pct >= -20:
        flag = "🟡"
    else:
        flag = "🔴"

    sign = "+" if diff_pct >= 0 else ""
    return f" {flag} (avg: {benchmark:.2f}, {sign}{diff_pct:.0f}%)"


# ============================================
# SELF-LEARNING SYSTEM
# ============================================

HISTORY_FILE = "/tmp/campaign_history.json"
HISTORY_RETENTION_DAYS = 90

# ============================================
# CLIENT GOALS CONFIG
# Definiuje cel każdego klienta — wpływa na to
# jakie metryki są ważne i jakie alerty się pokazują
# ============================================
CLIENT_GOALS = {
    # engagement/traffic — mierzy CTR, CPC, Reach, Frequency
    # NIE mierzy konwersji sprzedażowych ani ROAS
    "drzwi dre": "engagement",

    # conversion — mierzy ROAS, konwersje, CPA (domyślne dla reszty klientów)
    # "inny klient": "conversion",
}


def _load_history_raw():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_history_raw(data):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Błąd zapisu historii: {e}")


def save_campaign_results(client, campaign, metrics, actions_taken=None):
    """Zapisuje dzisiejsze wyniki kampanii do historii (90-dniowy retention)."""
    if actions_taken is None:
        actions_taken = []
    data = _load_history_raw()
    if client not in data:
        data[client] = {"campaigns": {}, "predictions": []}
    data[client].setdefault("campaigns", {})
    data[client].setdefault("predictions", [])

    today = datetime.now().strftime('%Y-%m-%d')
    dow = datetime.now().strftime('%A').lower()
    entry = {
        "date": today,
        "day_of_week": dow,
        "is_weekend": dow in ["saturday", "sunday"],
        "ctr": metrics.get("ctr"),
        "cpc": metrics.get("cpc"),
        "roas": metrics.get("roas"),
        "frequency": metrics.get("frequency"),
        "spend": metrics.get("spend", 0),
        "conversions": metrics.get("conversions", 0),
        "impressions": metrics.get("impressions", 0),
        "clicks": metrics.get("clicks", 0),
        "platform": metrics.get("platform", "meta"),
        "actions_taken": actions_taken,
    }

    if campaign not in data[client]["campaigns"]:
        data[client]["campaigns"][campaign] = []

    # Replace today's entry if exists
    data[client]["campaigns"][campaign] = [
        e for e in data[client]["campaigns"][campaign] if e.get("date") != today
    ]
    data[client]["campaigns"][campaign].append(entry)

    # Prune old entries
    cutoff = (datetime.now() - timedelta(days=HISTORY_RETENTION_DAYS)).strftime('%Y-%m-%d')
    data[client]["campaigns"][campaign] = [
        e for e in data[client]["campaigns"][campaign] if e.get("date", "") >= cutoff
    ]
    _save_history_raw(data)


def load_campaign_history(client, campaign=None, days_back=30):
    """Loads campaign history. Returns list (single campaign) or dict (all campaigns)."""
    data = _load_history_raw()
    campaigns = data.get(client, {}).get("campaigns", {})
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

    if campaign:
        return [e for e in campaigns.get(campaign, []) if e.get("date", "") >= cutoff]

    return {
        name: [e for e in entries if e.get("date", "") >= cutoff]
        for name, entries in campaigns.items()
        if any(e.get("date", "") >= cutoff for e in entries)
    }


def _save_prediction(client, campaign, recommendation, predicted_metric, predicted_change_pct, confidence):
    """Saves prediction for later accuracy evaluation."""
    data = _load_history_raw()
    if client not in data:
        data[client] = {"campaigns": {}, "predictions": []}
    data[client].setdefault("predictions", [])

    data[client]["predictions"].append({
        "date": datetime.now().strftime('%Y-%m-%d'),
        "campaign": campaign,
        "recommendation": recommendation,
        "predicted_metric": predicted_metric,
        "predicted_change_pct": predicted_change_pct,
        "confidence": confidence,
        "actual_change_pct": None,
        "verified": False,
    })

    cutoff = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    data[client]["predictions"] = [
        p for p in data[client]["predictions"] if p.get("date", "") >= cutoff
    ]
    _save_history_raw(data)


def calculate_confidence(pattern_count, success_count):
    """Returns confidence 0.0–1.0. Requires ≥2 observations to be nonzero."""
    if pattern_count < 2:
        return 0.0
    rate = success_count / pattern_count
    weight = min(pattern_count / 5.0, 1.0)  # max weight at 5+ observations
    return rate * weight


def analyze_patterns(client):
    """
    Analyzes 90-day history. Returns dict with:
    - summary.frequency_creative: CTR improvement after creative refresh when freq>4.5
    - summary.budget_increase: CPC impact after spend >+20%
    - summary.weekend: weekend vs weekday CTR/ROAS
    """
    all_history = load_campaign_history(client, days_back=90)
    freq_creative = []
    budget_impact = []
    weekend_wd, weekend_we = [], []
    ctr_recovery = []

    for campaign, entries in all_history.items():
        if len(entries) < 3:
            continue
        entries_s = sorted(entries, key=lambda x: x.get("date", ""))

        for i in range(1, len(entries_s)):
            prev = entries_s[i - 1]
            curr = entries_s[i]

            # Weekend performance bucket
            if curr.get("ctr"):
                bucket = weekend_we if curr.get("is_weekend") else weekend_wd
                bucket.append({"ctr": curr["ctr"], "roas": curr.get("roas"), "campaign": campaign})

            # Frequency spike → creative refresh → CTR change 48h later
            if (prev.get("frequency", 0) >= 4.5
                    and "creative_refresh" in curr.get("actions_taken", [])
                    and i + 1 < len(entries_s)):
                after = entries_s[i + 1]
                if prev.get("ctr") and after.get("ctr") and prev["ctr"] > 0:
                    imp = (after["ctr"] - prev["ctr"]) / prev["ctr"] * 100
                    freq_creative.append({
                        "campaign": campaign,
                        "freq_trigger": prev["frequency"],
                        "improvement_pct": imp,
                        "success": imp > 0,
                    })

            # Budget increase → CPC impact
            if prev.get("spend", 0) > 0 and curr.get("spend"):
                spend_chg = (curr["spend"] - prev["spend"]) / prev["spend"] * 100
                if spend_chg > 20 and prev.get("cpc") and curr.get("cpc"):
                    cpc_chg = (curr["cpc"] - prev["cpc"]) / prev["cpc"] * 100
                    budget_impact.append({
                        "campaign": campaign,
                        "spend_increase_pct": spend_chg,
                        "cpc_change_pct": cpc_chg,
                        "success": cpc_chg < 10,  # <10% CPC increase = acceptable
                    })

            # CTR change after any action
            for action in curr.get("actions_taken", []):
                if prev.get("ctr") and curr.get("ctr") and prev["ctr"] > 0:
                    chg = (curr["ctr"] - prev["ctr"]) / prev["ctr"] * 100
                    ctr_recovery.append({
                        "campaign": campaign, "action": action,
                        "ctr_change_pct": chg, "success": chg > 5,
                    })

    summary = {}

    if freq_creative:
        successes = [p for p in freq_creative if p["success"]]
        avg_imp = sum(p["improvement_pct"] for p in successes) / len(successes) if successes else 0
        summary["frequency_creative"] = {
            "total": len(freq_creative),
            "successes": len(successes),
            "avg_ctr_improvement_pct": avg_imp,
            "confidence": calculate_confidence(len(freq_creative), len(successes)),
        }

    if budget_impact:
        successes = [p for p in budget_impact if p["success"]]
        summary["budget_increase"] = {
            "total": len(budget_impact),
            "successes": len(successes),
            "confidence": calculate_confidence(len(budget_impact), len(successes)),
        }

    if weekend_wd and weekend_we:
        avg_wd_ctr = sum(d["ctr"] for d in weekend_wd) / len(weekend_wd)
        avg_we_ctr = sum(d["ctr"] for d in weekend_we) / len(weekend_we)
        wd_roas = [d["roas"] for d in weekend_wd if d.get("roas")]
        we_roas = [d["roas"] for d in weekend_we if d.get("roas")]
        avg_wd_roas = sum(wd_roas) / len(wd_roas) if wd_roas else 0
        avg_we_roas = sum(we_roas) / len(we_roas) if we_roas else 0
        summary["weekend"] = {
            "weekday_avg_ctr": avg_wd_ctr,
            "weekend_avg_ctr": avg_we_ctr,
            "ctr_diff_pct": (avg_we_ctr - avg_wd_ctr) / avg_wd_ctr * 100 if avg_wd_ctr else 0,
            "weekday_avg_roas": avg_wd_roas,
            "weekend_avg_roas": avg_we_roas,
            "roas_diff_pct": (avg_we_roas - avg_wd_roas) / avg_wd_roas * 100 if avg_wd_roas else 0,
        }

    return {
        "freq_creative_data": freq_creative,
        "budget_impact_data": budget_impact,
        "weekend_wd": weekend_wd,
        "weekend_we": weekend_we,
        "ctr_recovery": ctr_recovery,
        "summary": summary,
    }


def _confidence_label(conf):
    """Returns human-readable confidence label or None if below threshold."""
    if conf >= 0.90:
        return f"Strongly recommend ({conf * 100:.0f}%)"
    elif conf >= 0.70:
        return f"Recommend ({conf * 100:.0f}%)"
    elif conf >= 0.50:
        return f"Consider ({conf * 100:.0f}%)"
    return None


def generate_smart_recommendations(client, current_campaigns, patterns=None):
    """
    Generates ranked recommendations based on current metrics + learned patterns.
    Only returns items with confidence ≥50%.
    """
    if patterns is None:
        patterns = analyze_patterns(client)

    recs = []
    freq_p = patterns.get("summary", {}).get("frequency_creative", {})

    for c in current_campaigns:
        name = c.get("campaign_name", c.get("name", ""))
        if not name:
            continue
        freq = c.get("frequency")
        ctr = c.get("ctr")
        roas = c.get("purchase_roas", c.get("roas"))
        cpc = c.get("cpc")
        spend = c.get("spend", c.get("cost", 0))

        # --- Frequency → Creative Refresh ---
        if freq and freq >= 4.5:
            avg_imp = freq_p.get("avg_ctr_improvement_pct", 30.0)
            base = freq_p.get("confidence", 0.0) if freq_p.get("total", 0) >= 2 else 0.0
            conf = min(base + 0.30 + (freq - 4.5) * 0.05, 0.95)
            if conf >= 0.50:
                hist_note = (
                    f"{freq_p.get('successes', '?')}/{freq_p.get('total', '?')} razy dało CTR +{avg_imp:.0f}%"
                    if freq_p.get("total") else "benchmark branżowy (brak własnej historii)"
                )
                recs.append({
                    "campaign": name,
                    "action": "Wymień kreacje (Creative Refresh)",
                    "reason": f"Frequency {freq:.1f} ≥ 4.5 – ryzyko ad fatigue",
                    "evidence": hist_note,
                    "expected_impact": f"CTR +{avg_imp * 0.7:.0f}% – {avg_imp * 1.3:.0f}%",
                    "confidence": conf,
                    "urgency": "🔴" if freq >= 6.0 else "🟡",
                    "predicted_metric": "ctr",
                    "predicted_change_pct": avg_imp,
                })

        # --- Low CTR → targeting review ---
        if ctr is not None and ctr < 0.6:
            recs.append({
                "campaign": name,
                "action": "Zmień targeting / grupę odbiorców",
                "reason": f"CTR {ctr:.2f}% < 0.6% (bardzo niski)",
                "evidence": "Mismatching audience lub silna ad fatigue",
                "expected_impact": "CTR +0.3-0.8 pp po zmianie targetingu",
                "confidence": 0.72,
                "urgency": "🟡",
                "predicted_metric": "ctr",
                "predicted_change_pct": 50.0,
            })

        # --- ROAS below break-even ---
        if roas is not None and roas < 1.5 and spend > 50:
            recs.append({
                "campaign": name,
                "action": "Pause lub głęboka optymalizacja",
                "reason": f"ROAS {roas:.2f}x – poniżej break-even (marża 40%)",
                "evidence": "ROAS <1.5x = strata na każdej transakcji",
                "expected_impact": "Oszczędność budżetu lub ROAS +60% po optymalizacji",
                "confidence": 0.80,
                "urgency": "🔴",
                "predicted_metric": "roas",
                "predicted_change_pct": 60.0,
            })

        # --- High CPC ---
        if cpc is not None and cpc > 15:
            recs.append({
                "campaign": name,
                "action": "Zmień strategię bidowania (Target CPA)",
                "reason": f"CPC {cpc:.2f} PLN > 15 PLN",
                "evidence": "Target CPA zazwyczaj obniża CPC o 20-30% vs manual",
                "expected_impact": "CPC -20-30%",
                "confidence": 0.65,
                "urgency": "🟡",
                "predicted_metric": "cpc",
                "predicted_change_pct": -25.0,
            })

    # --- Weekend dayparting ---
    weekend = patterns.get("summary", {}).get("weekend", {})
    if weekend and weekend.get("roas_diff_pct", 0) > 10:
        diff = weekend["roas_diff_pct"]
        recs.append({
            "campaign": "WSZYSTKIE kampanie",
            "action": "Dayparting – zwiększ budżet w weekendy",
            "reason": f"ROAS w weekendy +{diff:.0f}% vs dni robocze",
            "evidence": (
                f"Weekday avg ROAS: {weekend['weekday_avg_roas']:.2f}x | "
                f"Weekend: {weekend['weekend_avg_roas']:.2f}x"
            ),
            "expected_impact": f"+{diff * 0.4:.0f}% efektywności budżetu",
            "confidence": min(0.50 + abs(diff) / 100, 0.85),
            "urgency": "💡",
            "predicted_metric": "roas",
            "predicted_change_pct": diff * 0.4,
        })

    recs.sort(key=lambda x: x["confidence"], reverse=True)
    return [r for r in recs if r["confidence"] >= 0.50]


def suggest_experiments(client, current_campaigns):
    """Suggests A/B tests for placements/features never tried before."""
    all_history = load_campaign_history(client, days_back=90)
    known_names = set()
    for camp_list in all_history.values():
        for entry in camp_list:
            known_names.add(entry.get("campaign_name", "").lower())
    for c in current_campaigns:
        known_names.add(c.get("campaign_name", c.get("name", "")).lower())

    experiment_pool = [
        {
            "name": "Instagram Reels",
            "keywords": ["reels"],
            "expected": "CTR 1.8-2.5%",
            "budget": "200 PLN / 7 dni",
            "reason": "Reels mają ~40% niższy CPM vs feed – nigdy niespróbowane dla DRE",
        },
        {
            "name": "Stories",
            "keywords": ["stories", "story"],
            "expected": "CTR 1.5-2.0%",
            "budget": "150 PLN / 7 dni",
            "reason": "Stories świetne dla produktów fizycznych – niespróbowane",
        },
        {
            "name": "Advantage+ Shopping Campaign",
            "keywords": ["advantage", "adv+", "asc"],
            "expected": "ROAS +30-50% vs standard",
            "budget": "300 PLN / 14 dni",
            "reason": "ASC automatycznie optymalizuje kreacje i targeting – nieprzetestowane",
        },
        {
            "name": "Google Performance Max",
            "keywords": ["pmax", "performance max"],
            "expected": "Szerszy zasięg (Search+Display+YouTube)",
            "budget": "500 PLN / 14 dni",
            "reason": "PMax pokrywa wszystkie kanały Google jednocześnie – nieprzetestowane",
        },
    ]

    suggestions = []
    for exp in experiment_pool:
        tested = any(any(kw in n for kw in exp["keywords"]) for n in known_names)
        if not tested:
            suggestions.append({
                "experiment": f"Test: {exp['name']}",
                "reason": exp["reason"],
                "expected": exp["expected"],
                "budget": exp["budget"],
                "confidence": 0.70,
            })

    return suggestions[:3]


def generate_weekly_learnings(client="dre"):
    """
    Weekly summary of:
    - Predictions vs actual results (accuracy score)
    - Learned patterns (frequency/creative, weekend, budget)
    """
    data = _load_history_raw()
    predictions = data.get(client, {}).get("predictions", [])
    cutoff = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    week_preds = [p for p in predictions if p.get("date", "") >= cutoff]

    patterns = analyze_patterns(client)
    summary = patterns.get("summary", {})
    text = "🧠 **WEEKLY LEARNINGS – Co nauczyłem się w tym tygodniu:**\n\n"

    # Evaluate predictions
    if week_preds:
        all_hist = load_campaign_history(client, days_back=30)
        verified = []
        for pred in week_preds:
            camp_hist = all_hist.get(pred["campaign"], [])
            after_date = (datetime.strptime(pred["date"], '%Y-%m-%d') + timedelta(days=2)).strftime('%Y-%m-%d')
            before = [e for e in camp_hist if e.get("date", "") < after_date]
            after = [e for e in camp_hist if e.get("date", "") >= after_date]
            metric = pred.get("predicted_metric", "ctr")
            if before and after:
                bv = before[-1].get(metric)
                av = after[0].get(metric)
                if bv and av and bv > 0:
                    actual_chg = (av - bv) / bv * 100
                    pred_chg = pred.get("predicted_change_pct", 0)
                    success = (actual_chg > 0) == (pred_chg > 0)
                    verified.append({**pred, "actual_change_pct": actual_chg, "success": success})

        if verified:
            for v in verified[:4]:
                icon = "✅" if v["success"] else "❌"
                text += f"{icon} **{v['campaign']}** – {v['recommendation']}\n"
                text += f"   Predicted: {v.get('predicted_change_pct', 0):+.0f}% | "
                text += f"Actual: {v.get('actual_change_pct', 0):+.0f}%\n\n"
            acc = sum(1 for v in verified if v["success"]) / len(verified) * 100
            text += f"🎯 **Accuracy: {acc:.0f}%** ({len(verified)} predictions verified)\n\n"
            text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    # Pattern insights
    freq_p = summary.get("frequency_creative", {})
    if freq_p and freq_p.get("total", 0) >= 2:
        text += f"📌 **Creative refresh pattern** ({freq_p['total']} obserwacji):\n"
        text += f"   {freq_p['successes']}/{freq_p['total']} razy pomogło"
        text += f" | Avg CTR +{freq_p['avg_ctr_improvement_pct']:.0f}%\n\n"

    weekend = summary.get("weekend", {})
    if weekend:
        ctr_d = weekend.get("ctr_diff_pct", 0)
        roas_d = weekend.get("roas_diff_pct", 0)
        we_count = len(patterns.get("weekend_we", []))
        text += f"📌 **Weekend vs Weekday** ({we_count} weekend-dni):\n"
        text += f"   CTR: {'🟢 +' if ctr_d > 0 else '🔴 '}{abs(ctr_d):.1f}% w weekendy\n"
        text += f"   ROAS: {'🟢 +' if roas_d > 0 else '🔴 '}{abs(roas_d):.1f}% w weekendy\n\n"

    budget_p = summary.get("budget_increase", {})
    if budget_p and budget_p.get("total", 0) >= 2:
        text += f"📌 **Budget increase pattern** ({budget_p['total']} obserwacji):\n"
        text += f"   {budget_p['successes']}/{budget_p['total']} razy CPC nie wzrósł >10%\n\n"

    if not freq_p and not weekend and not week_preds:
        text += "ℹ️ Za mało danych historycznych – bot zbiera dane od dziś.\n"
        text += "Po 2-3 tygodniach działania zacznę wykrywać wzorce i weryfikować własne rekomendacje.\n"

    return text


def weekly_learnings_dre():
    """Wysyła weekly learnings w poniedziałek 8:30."""
    try:
        dre_channel = os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")
        logger.info("🧠 Generuję Weekly Learnings DRE...")
        text = generate_weekly_learnings("dre")
        app.client.chat_postMessage(channel=dre_channel, text=text)
        logger.info("✅ Weekly Learnings wysłane!")
    except Exception as e:
        logger.error(f"❌ Błąd weekly_learnings_dre: {e}")


def generate_daily_digest_dre():
    """
    Generuje daily digest dla klienta DRE (Meta + Google Ads) z benchmarkami.
    """
    try:
        # Pobierz dane z wczoraj
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')

        # Pobierz benchmarki (30 dni) równolegle z danymi dziennymi
        meta_benchmarks = get_client_benchmarks("drzwi dre", "meta", lookback_days=30)
        google_benchmarks = get_client_benchmarks("dre", "google", lookback_days=30)

        # === META ADS ===
        # Cel klienta DRE: engagement (nie konwersje sprzedażowe)
        client_goal = CLIENT_GOALS.get("drzwi dre", "conversion")

        meta_data = meta_ads_tool(
            client_name="drzwi dre",
            date_from=yesterday,
            date_to=today,
            level="campaign",
            metrics=["campaign_name", "spend", "impressions", "clicks", "ctr", "cpc",
                    "reach", "frequency", "conversions", "purchase_roas"]
        )

        # === GOOGLE ADS ===
        google_data_combined = []

        for account in ["dre", "dre 2024", "dre 2025"]:
            data = google_ads_tool(
                client_name=account,
                date_from=yesterday,
                date_to=today,
                level="campaign",
                metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                        "metrics.cost_micros", "metrics.conversions", "metrics.ctr",
                        "metrics.average_cpc"]
            )

            if data.get("data"):
                google_data_combined.extend(data["data"])

        # Połącz dane Meta + Google
        all_campaigns_raw = []
        meta_campaigns_raw = meta_data.get("data", [])

        if meta_campaigns_raw:
            all_campaigns_raw.extend(meta_campaigns_raw)
        all_campaigns_raw.extend(google_data_combined)

        if not all_campaigns_raw:
            return "📊 DRE - Daily Digest\n\n⚠️ Brak danych za wczoraj. Sprawdź czy kampanie są aktywne."

        # Filtruj kampanie z minimalnym spendem (>= 20 PLN)
        MIN_SPEND_PLN = 20.0
        meta_campaigns = [c for c in meta_campaigns_raw
                          if float(c.get("spend", 0) or 0) >= MIN_SPEND_PLN]
        google_data_combined = [c for c in google_data_combined
                                if float(c.get("cost", c.get("spend", 0)) or 0) >= MIN_SPEND_PLN]
        all_campaigns = meta_campaigns + google_data_combined

        skipped_count = len(all_campaigns_raw) - len(all_campaigns)

        if not all_campaigns:
            return "📊 DRE - Daily Digest\n\n⚠️ Brak kampanii z spendem ≥ 20 PLN za wczoraj."

        # === SAVE RESULTS TO HISTORY (zapisuj wszystkie, niezależnie od spędu) ===
        for c in meta_campaigns_raw:
            name = c.get("campaign_name", "")
            if name:
                save_campaign_results("dre", name, {
                    "ctr": c.get("ctr"),
                    "cpc": c.get("cpc"),
                    "roas": c.get("purchase_roas"),
                    "frequency": c.get("frequency"),
                    "spend": c.get("spend", 0),
                    "conversions": c.get("conversions", 0),
                    "impressions": c.get("impressions", 0),
                    "clicks": c.get("clicks", 0),
                    "platform": "meta",
                })
        google_data_raw = [c for c in all_campaigns_raw if c not in meta_campaigns_raw]
        for c in google_data_raw:
            name = c.get("campaign_name", c.get("name", ""))
            if name:
                save_campaign_results("dre", name, {
                    "ctr": c.get("ctr"),
                    "cpc": c.get("cpc"),
                    "roas": None,
                    "spend": c.get("cost", c.get("spend", 0)),
                    "conversions": c.get("conversions", 0),
                    "impressions": c.get("impressions", 0),
                    "clicks": c.get("clicks", 0),
                    "platform": "google",
                })

        # Analizuj trendy (z uwzględnieniem celu klienta)
        analysis = analyze_campaign_trends(all_campaigns, goal=client_goal)

        # Oblicz totals
        total_spend = sum(c.get("spend", 0) or c.get("cost", 0) for c in all_campaigns)
        total_clicks = sum(c.get("clicks", 0) for c in all_campaigns)
        total_impressions = sum(c.get("impressions", 0) for c in all_campaigns)
        total_reach = sum(c.get("reach", 0) for c in all_campaigns)

        # Zbuduj digest
        skipped_note = f" _(+ {skipped_count} kampanii z <20 PLN pominięto)_" if skipped_count > 0 else ""

        if client_goal == "engagement":
            goal_line = f"👁️ **Impressions:** {total_impressions:,}\n👥 **Reach:** {total_reach:,}"
        else:
            total_conversions = sum(c.get("conversions", 0) for c in all_campaigns)
            goal_line = f"🎯 **Conversions:** {total_conversions}"

        digest = f"""🌅 **DRE - Daily Digest** ({yesterday})

💰 **WCZORAJ:** {total_spend:.2f} PLN
📊 **Aktywnych kampanii:** {len(all_campaigns)}{skipped_note}
{goal_line}
👆 **Clicks:** {total_clicks:,}

━━━━━━━━━━━━━━━━━━━━━━
"""

        # === META - szczegóły kampanii z benchmarkami ===
        if meta_campaigns and meta_benchmarks:
            digest += "\n📘 **META ADS - Kampanie vs 30-dni benchmark:**\n\n"
            for c in meta_campaigns:
                name = c.get("campaign_name", "?")
                spend = c.get("spend", 0)
                ctr = c.get("ctr")
                cpc = c.get("cpc")
                roas = c.get("purchase_roas")
                freq = c.get("frequency")
                reach = c.get("reach", 0)
                impressions = c.get("impressions", 0)
                convs = c.get("conversions", 0)

                if client_goal == "engagement":
                    # Engagement: pokaż reach/impressions zamiast konwersji
                    digest += f"**{name}**  💰 {spend:.2f} PLN | 👥 {reach:,} reach\n"
                else:
                    digest += f"**{name}**  💰 {spend:.2f} PLN | 🎯 {convs} conv\n"

                if ctr is not None:
                    digest += f"  - CTR: {ctr:.2f}%{_benchmark_flag(ctr, meta_benchmarks.get('avg_ctr'), higher_is_better=True)}\n"
                if cpc is not None:
                    digest += f"  - CPC: {cpc:.2f} PLN{_benchmark_flag(cpc, meta_benchmarks.get('avg_cpc'), higher_is_better=False)}\n"
                if client_goal == "engagement":
                    # Engagement: pokaż impressions i frequency zamiast ROAS
                    if impressions:
                        digest += f"  - Impressions: {impressions:,}\n"
                    if freq is not None:
                        freq_warn = " ⚠️ ad fatigue!" if freq > 4 else (" 👀 obserwuj" if freq > 2.5 else "")
                        digest += f"  - Frequency: {freq:.1f}{freq_warn}{_benchmark_flag(freq, meta_benchmarks.get('avg_frequency'), higher_is_better=False)}\n"
                else:
                    # Conversion: pokaż ROAS i frequency
                    if roas is not None:
                        digest += f"  - ROAS: {roas:.2f}x{_benchmark_flag(roas, meta_benchmarks.get('avg_roas'), higher_is_better=True)}\n"
                    if freq is not None:
                        digest += f"  - Freq: {freq:.1f}{_benchmark_flag(freq, meta_benchmarks.get('avg_frequency'), higher_is_better=False)}\n"
                digest += "\n"
            digest += "━━━━━━━━━━━━━━━━━━━━━━\n"

        # === GOOGLE - szczegóły kampanii z benchmarkami ===
        if google_data_combined and google_benchmarks:
            digest += "\n🔵 **GOOGLE ADS - Kampanie vs 30-dni benchmark:**\n\n"
            for c in google_data_combined:
                name = c.get("campaign_name", c.get("name", "?"))
                spend = c.get("cost", c.get("spend", 0))
                ctr = c.get("ctr")
                cpc = c.get("cpc")
                convs = c.get("conversions", 0)
                clicks = c.get("clicks", 0)

                if client_goal == "engagement":
                    digest += f"**{name}**  💰 {spend:.2f} PLN | 👆 {clicks:,} clicks\n"
                else:
                    digest += f"**{name}**  💰 {spend:.2f} PLN | 🎯 {convs} conv\n"

                if ctr is not None:
                    digest += f"  - CTR: {ctr:.2f}%{_benchmark_flag(ctr, google_benchmarks.get('avg_ctr'), higher_is_better=True)}\n"
                if cpc is not None:
                    digest += f"  - CPC: {cpc:.2f} PLN{_benchmark_flag(cpc, google_benchmarks.get('avg_cpc'), higher_is_better=False)}\n"
                digest += "\n"
            digest += "━━━━━━━━━━━━━━━━━━━━━━\n"

        # CRITICAL ALERTS
        if analysis["critical_alerts"]:
            digest += "\n🔴 **CRITICAL - WYMAGA AKCJI:**\n\n"
            for alert in analysis["critical_alerts"][:3]:  # Top 3
                campaign = alert["campaign"]

                # Sprawdź historię jeśli zero conversions
                if alert["type"] == "zero_conversions":
                    history = check_conversion_history("dre", "google", campaign)

                    if history["had_conversions"]:
                        digest += f"**{campaign}**\n"
                        digest += f"⚠️ Zero conversions (miała {history['total']} w ostatnich 30 dni)\n"
                        digest += f"💰 Spend: {alert['spend']:.2f} PLN\n"
                        digest += f"💡 **AKCJA:** Sprawdź tracking/landing page!\n\n"
                    else:
                        digest += f"**{campaign}**\n"
                        digest += f"🟡 Zero conversions (ta kampania nie generuje conversions)\n"
                        digest += f"💡 Rozważ pause lub zmianę celu\n\n"

                elif alert["type"] == "low_ctr":
                    digest += f"**{campaign}**\n"
                    digest += f"📉 {alert['message']}\n"
                    digest += f"💡 **AKCJA:** Wymień kreacje (ad fatigue)\n\n"

            digest += "━━━━━━━━━━━━━━━━━━━━━━\n"

        # WARNINGS
        if analysis["warnings"]:
            digest += "\n🟡 **DO OBEJRZENIA:**\n\n"
            for warning in analysis["warnings"][:2]:  # Top 2
                digest += f"• **{warning['campaign']}** - {warning['message']}\n"
            digest += "\n━━━━━━━━━━━━━━━━━━━━━━\n"

        # TOP PERFORMERS
        if analysis["top_performers"]:
            digest += "\n🟢 **TOP PERFORMERS:**\n\n"
            for i, top in enumerate(analysis["top_performers"], 1):
                digest += f"{i}. **{top['campaign']}**\n"
                if client_goal == "engagement":
                    ctr_val = top.get('ctr', 0)
                    cpc_val = top.get('cpc', 0)
                    reach_val = top.get('reach', 0)
                    digest += f"   CTR {ctr_val:.2f}% | CPC {cpc_val:.2f} PLN | 👥 {reach_val:,} reach\n"
                else:
                    digest += f"   ROAS {top['roas']:.1f} | {top['conversions']} conversions | {top['spend']:.2f} PLN\n"
            digest += "\n"

        # Footer
        if not analysis["critical_alerts"] and not analysis["warnings"]:
            digest += "\n✅ **Wszystko OK!** Żadnych critical issues.\n"

        # Benchmark footer
        if meta_benchmarks:
            digest += f"\n_📊 Benchmarki z ostatnich {meta_benchmarks['period_days']} dni ({meta_benchmarks['campaign_count']} kampanii Meta)_\n"

        # === SMART RECOMMENDATIONS (AI-learned) ===
        try:
            patterns = analyze_patterns("dre")
            recs = generate_smart_recommendations("dre", all_campaigns, patterns)
            experiments = suggest_experiments("dre", all_campaigns)

            if recs or experiments:
                digest += "\n━━━━━━━━━━━━━━━━━━━━━━\n"
                digest += "\n🧠 **SMART RECOMMENDATIONS (AI-learned):**\n\n"

                shown = 0
                for rec in recs[:4]:
                    label = _confidence_label(rec["confidence"])
                    if not label:
                        continue
                    shown += 1
                    digest += f"{shown}. {rec['urgency']} **{rec['campaign']}** – {rec['action']}\n"
                    digest += f"   Dlaczego: {rec['reason']}\n"
                    digest += f"   Historia: {rec['evidence']}\n"
                    digest += f"   Expected: {rec['expected_impact']}\n"
                    digest += f"   Confidence: **{label}**\n\n"

                    # Save prediction for accuracy tracking
                    _save_prediction(
                        "dre", rec["campaign"], rec["action"],
                        rec.get("predicted_metric", "ctr"),
                        rec.get("predicted_change_pct", 20.0),
                        rec["confidence"],
                    )

                if experiments:
                    digest += "💡 **EKSPERYMENTY DO PRZETESTOWANIA:**\n\n"
                    for exp in experiments:
                        digest += f"🧪 **{exp['experiment']}**\n"
                        digest += f"   Dlaczego: {exp['reason']}\n"
                        digest += f"   Expected: {exp['expected']}\n"
                        digest += f"   Budget: {exp['budget']}\n\n"

                if not recs and not experiments:
                    digest += "_Brak rekomendacji – za mało danych historycznych. Bot uczy się z każdym dniem._\n"

        except Exception as e:
            logger.error(f"Błąd smart recommendations w digest: {e}")

        return digest

    except Exception as e:
        logger.error(f"Błąd generowania digestu: {e}")
        return f"❌ Błąd generowania digestu: {str(e)}"

def daily_digest_dre():
    """Wysyła daily digest dla DRE o 9:00"""
    try:
        dre_channel_id = os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")
        
        logger.info("🔥 Generuję Daily Digest dla DRE...")
        
        digest = generate_daily_digest_dre()
        
        app.client.chat_postMessage(
            channel=dre_channel_id,
            text=digest
        )
        
        logger.info("✅ Daily Digest wysłany!")
        
    except Exception as e:
        logger.error(f"❌ Błąd wysyłania digestu: {e}")
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

# ============================================
# TEMPLATE SYSTEM - formatowanie wiadomości
# ============================================

def format_budget_alert(alert):
    """Formatuje alert budżetowy"""
    emoji = "🔴" if alert["level"] == "CRITICAL" else "🟡"
    action = "⛔ AKCJA: Zredukuj budget TERAZ!" if alert["level"] == "CRITICAL" else "👀 Monitoruj - możliwy overspend"
    return (
        f"{emoji} *BUDGET ALERT - {alert['level']}*\n"
        f"📌 Klient: {alert['client'].upper()} ({alert['platform']})\n"
        f"📢 Kampania: {alert['campaign']}\n"
        f"💰 Spend dzisiaj: {alert['spend']:.2f} PLN\n"
        f"📈 Pace: {alert['pace']:.0f}% daily budget\n"
        f"{action}"
    )

def format_weekly_summary(client_name, data, period):
    """Formatuje tygodniowy raport dla klienta"""
    if not data:
        return f"📊 *{client_name.upper()}* - brak danych za {period}"

    total_spend = sum(c.get("spend", 0) or c.get("cost", 0) for c in data)
    total_conversions = sum(c.get("conversions", 0) for c in data)
    total_clicks = sum(c.get("clicks", 0) for c in data)

    roas_values = [c.get("purchase_roas", 0) for c in data if c.get("purchase_roas", 0) > 0]
    avg_roas = sum(roas_values) / len(roas_values) if roas_values else 0

    analysis = analyze_campaign_trends(data)

    roas_line = ""
    if avg_roas > 0:
        roas_emoji = "✅" if avg_roas >= 3.0 else ("🟡" if avg_roas >= 2.0 else "🔴")
        roas_line = f"📈 Avg ROAS: {avg_roas:.2f} {roas_emoji}\n"

    report = (
        f"📊 *{client_name.upper()} - Weekly Report* ({period})\n\n"
        f"💰 SPEND: {total_spend:.2f} PLN\n"
        f"🎯 Conversions: {total_conversions}\n"
        f"👆 Clicks: {total_clicks:,}\n"
        f"{roas_line}"
        f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if analysis["critical_alerts"]:
        report += "\n🔴 *WYMAGA UWAGI:*\n"
        for alert in analysis["critical_alerts"][:3]:
            report += f"• {alert['campaign']}: {alert['message']}\n"

    if analysis["top_performers"]:
        top_p = analysis["top_performers"][0]
        report += f"\n🔥 *TOP:* {top_p['campaign']} | ROAS {top_p['roas']:.1f} | {top_p['conversions']} conv\n"

    if analysis["warnings"]:
        report += "\n🟡 *DO OBEJRZENIA:*\n"
        for w in analysis["warnings"][:2]:
            report += f"• {w['campaign']}: {w['message']}\n"

    return report


# ============================================
# BUDGET ALERTS - REAL-TIME (co godzinę)
# ============================================

sent_alerts = {}  # {alert_key: datetime} - cooldown tracking

def should_send_alert(alert_key, cooldown_hours=4):
    """Sprawdza czy alert był już wysłany w ostatnich X godzinach"""
    if alert_key in sent_alerts:
        hours_ago = (datetime.now() - sent_alerts[alert_key]).total_seconds() / 3600
        if hours_ago < cooldown_hours:
            return False
    return True

def mark_alert_sent(alert_key):
    sent_alerts[alert_key] = datetime.now()

def check_budget_alerts():
    """
    Sprawdza budget pace dla wszystkich klientów i wysyła alerty.
    Uruchamiane co godzinę (7:00-22:00).
    """
    try:
        warsaw_tz = pytz.timezone('Europe/Warsaw')
        now = datetime.now(warsaw_tz)

        # Cicho w nocy
        if now.hour < 7 or now.hour >= 22:
            return

        day_progress = (now.hour * 60 + now.minute) / (24 * 60)
        today = now.strftime('%Y-%m-%d')

        alerts_to_send = []

        # === META ADS ===
        clients_meta = [
            ("drzwi dre", os.environ.get("DRE_CHANNEL_ID")),
            ("instax/fuji", os.environ.get("INSTAX_CHANNEL_ID")),
            ("zbiorcze", os.environ.get("GENERAL_CHANNEL_ID")),
        ]

        for client_name, channel_id in clients_meta:
            if not channel_id:
                continue
            try:
                data = meta_ads_tool(
                    client_name=client_name,
                    date_from=today,
                    date_to=today,
                    level="campaign",
                    metrics=["campaign_name", "spend", "budget_remaining"]
                )
                for campaign in data.get("data", []):
                    spend = float(campaign.get("spend", 0))
                    remaining = campaign.get("budget_remaining")
                    if spend < 10 or remaining is None:
                        continue
                    total_budget = spend + float(remaining)
                    if total_budget <= 0:
                        continue
                    pace = (spend / total_budget) / max(day_progress, 0.01)
                    campaign_name = campaign.get("campaign_name", "Unknown")
                    base_key = f"meta_{client_name}_{campaign_name}_{today}"

                    if pace > 1.5 and should_send_alert(base_key + "_crit"):
                        alerts_to_send.append({
                            "level": "CRITICAL", "platform": "Meta",
                            "client": client_name, "campaign": campaign_name,
                            "spend": spend, "pace": pace * 100,
                            "channel": channel_id, "alert_key": base_key + "_crit"
                        })
                    elif pace > 1.2 and should_send_alert(base_key + "_warn"):
                        alerts_to_send.append({
                            "level": "WARNING", "platform": "Meta",
                            "client": client_name, "campaign": campaign_name,
                            "spend": spend, "pace": pace * 100,
                            "channel": channel_id, "alert_key": base_key + "_warn"
                        })
            except Exception as e:
                logger.error(f"Budget alert Meta {client_name}: {e}")

        # Wyślij alerty
        for alert in alerts_to_send:
            try:
                app.client.chat_postMessage(
                    channel=alert["channel"],
                    text=format_budget_alert(alert)
                )
                mark_alert_sent(alert["alert_key"])
                logger.info(f"Budget alert: {alert['level']} - {alert['campaign']}")
            except Exception as e:
                logger.error(f"Błąd wysyłania alertu: {e}")

    except Exception as e:
        logger.error(f"Błąd check_budget_alerts: {e}")


# ============================================
# BUDGET ALERTS DRE - real-time monitoring
# ============================================

def check_budget_status(client_name, platform):
    """
    Pobiera spend vs daily budget dla klienta.
    Zwraca listę kampanii z alertami: >80% 🟡, >90% 🟠, >100% 🔴
    """
    today = datetime.now().strftime('%Y-%m-%d')
    alerts = []

    try:
        if platform == "meta":
            data = meta_ads_tool(
                client_name=client_name,
                date_from=today,
                date_to=today,
                level="campaign",
                metrics=["campaign_name", "spend", "budget_remaining"]
            )
            for campaign in data.get("data", []):
                spend = float(campaign.get("spend", 0))
                remaining = campaign.get("budget_remaining")
                if spend < 1 or remaining is None:
                    continue
                total = spend + float(remaining)
                if total <= 0:
                    continue
                pct = (spend / total) * 100
                if pct >= 80:
                    alerts.append({
                        "campaign": campaign.get("campaign_name", "Unknown"),
                        "spend": spend,
                        "total": total,
                        "pct": pct
                    })

        elif platform == "google":
            data = google_ads_tool(
                client_name=client_name,
                date_from=today,
                date_to=today,
                level="campaign",
                metrics=["campaign.name", "metrics.cost_micros"]
            )
            # Google Ads nie zwraca daily budget przez insights —
            # logujemy spend bez procentu (brak budget_remaining)
            for campaign in data.get("data", []):
                cost = campaign.get("cost", 0)
                if cost > 10:
                    alerts.append({
                        "campaign": campaign.get("name", "Unknown"),
                        "spend": cost,
                        "total": None,
                        "pct": None
                    })

    except Exception as e:
        logger.error(f"check_budget_status {client_name}/{platform}: {e}")

    return alerts


def send_budget_alerts_dre():
    """
    Sprawdza budgety dla wszystkich kont DRE i wysyła alert na #drzwi-dre.
    Uruchamiane co 2 godziny (9:00-19:00).
    """
    try:
        warsaw_tz = pytz.timezone('Europe/Warsaw')
        now = datetime.now(warsaw_tz)

        if now.hour < 9 or now.hour >= 19:
            return

        dre_channel = os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")
        alert_lines = []

        # === META ===
        meta_alerts = check_budget_status("drzwi dre", "meta")
        for a in meta_alerts:
            pct = a["pct"]
            emoji = "🔴" if pct >= 100 else ("🟠" if pct >= 90 else "🟡")
            line = f"{emoji} [Meta] {a['campaign']}: {a['spend']:.0f}/{a['total']:.0f} PLN ({pct:.0f}%)"
            alert_lines.append((pct, line))

        # === GOOGLE ===
        for account in ["dre", "dre 2024", "dre 2025"]:
            google_alerts = check_budget_status(account, "google")
            for a in google_alerts:
                line = f"📊 [Google/{account}] {a['campaign']}: {a['spend']:.0f} PLN spend today"
                alert_lines.append((0, line))

        if not alert_lines:
            return

        # Sortuj: najwyższy % najpierw
        alert_lines.sort(key=lambda x: x[0], reverse=True)

        msg = f"⚠️ *BUDGET ALERT - DRE* ({now.strftime('%H:%M')})\n\n"
        msg += "\n".join(line for _, line in alert_lines)
        msg += "\n\n_Sprawdź kampanie i zredukuj budget jeśli potrzeba._"

        app.client.chat_postMessage(channel=dre_channel, text=msg)
        logger.info(f"Budget alert DRE wysłany: {len(alert_lines)} kampanii")

    except Exception as e:
        logger.error(f"Błąd send_budget_alerts_dre: {e}")


# ============================================
# WEEKLY AUTO-REPORTS DRE - piątek 16:00
# ============================================

def generate_weekly_report_dre():
    """
    Generuje tygodniowy raport DRE z week-over-week comparison.
    Meta + Google, top/worst performers, rekomendacje.
    """
    now = datetime.now()
    # Ten tydzień: ostatnie 7 dni
    date_to = now.strftime('%Y-%m-%d')
    date_from = (now - timedelta(days=7)).strftime('%Y-%m-%d')
    # Poprzedni tydzień: 8-14 dni temu
    prev_to = (now - timedelta(days=8)).strftime('%Y-%m-%d')
    prev_from = (now - timedelta(days=14)).strftime('%Y-%m-%d')
    period_label = f"{(now - timedelta(days=7)).strftime('%d.%m')} - {now.strftime('%d.%m.%Y')}"

    def fetch_dre_data(d_from, d_to):
        campaigns = []
        # Meta
        meta = meta_ads_tool(
            client_name="drzwi dre",
            date_from=d_from, date_to=d_to,
            level="campaign",
            metrics=["campaign_name", "spend", "clicks", "impressions",
                     "ctr", "cpc", "conversions", "purchase_roas", "frequency"]
        )
        if meta.get("data"):
            for c in meta["data"]:
                c["_platform"] = "Meta"
            campaigns.extend(meta["data"])
        # Google
        for account in ["dre", "dre 2024", "dre 2025"]:
            g = google_ads_tool(
                client_name=account,
                date_from=d_from, date_to=d_to,
                level="campaign",
                metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                         "metrics.cost_micros", "metrics.conversions", "metrics.ctr",
                         "metrics.average_cpc"]
            )
            if g.get("data"):
                for c in g["data"]:
                    c["_platform"] = f"Google/{account}"
                    # normalize field names
                    c.setdefault("campaign_name", c.get("name", "Unknown"))
                    c.setdefault("spend", c.get("cost", 0))
                campaigns.extend(g["data"])
        return campaigns

    try:
        this_week = fetch_dre_data(date_from, date_to)
        prev_week = fetch_dre_data(prev_from, prev_to)

        if not this_week:
            return "📊 *DRE Weekly Report* - brak danych za ten tydzień."

        # === TOTALS ===
        def totals(data):
            return {
                "spend": sum(c.get("spend", 0) or c.get("cost", 0) for c in data),
                "conversions": sum(c.get("conversions", 0) for c in data),
                "clicks": sum(c.get("clicks", 0) for c in data),
            }

        cur = totals(this_week)
        prv = totals(prev_week)

        def delta(cur_val, prv_val):
            if prv_val == 0:
                return ""
            pct = ((cur_val - prv_val) / prv_val) * 100
            arrow = "↑" if pct >= 0 else "↓"
            return f" ({arrow}{abs(pct):.0f}% vs prev week)"

        # === TOP / WORST PERFORMERS (Meta - mamy ROAS) ===
        meta_camps = [c for c in this_week if c.get("_platform") == "Meta" and c.get("purchase_roas", 0) > 0]
        meta_camps_sorted = sorted(meta_camps, key=lambda x: x.get("purchase_roas", 0), reverse=True)
        top3 = meta_camps_sorted[:3]
        worst3 = meta_camps_sorted[-3:][::-1] if len(meta_camps_sorted) >= 3 else []

        # === REKOMENDACJE ===
        recommendations = []
        for c in worst3:
            roas = c.get("purchase_roas", 0)
            freq = c.get("frequency", 0)
            ctr = c.get("ctr", 0)
            name = c.get("campaign_name", "?")
            if roas < 2.0:
                recommendations.append(f"🔴 Pause lub optymalizuj *{name}* (ROAS {roas:.1f})")
            elif freq > 4:
                recommendations.append(f"🟡 Odśwież kreacje *{name}* (Frequency {freq:.1f})")
            elif ctr < 0.8:
                recommendations.append(f"🟡 Zmień targeting *{name}* (CTR {ctr:.2f}%)")

        for c in top3[:1]:
            name = c.get("campaign_name", "?")
            roas = c.get("purchase_roas", 0)
            recommendations.append(f"🚀 Skaluj *{name}* (ROAS {roas:.1f} — top performer!)")

        if not recommendations:
            recommendations.append("✅ Wszystkie kampanie w normie — monitoruj dalej.")

        # === BUDUJ RAPORT ===
        report = f"📊 *DRE - Weekly Report* ({period_label})\n\n"

        report += (
            f"💰 *SPEND:* {cur['spend']:.0f} PLN{delta(cur['spend'], prv['spend'])}\n"
            f"🎯 *CONVERSIONS:* {cur['conversions']}{delta(cur['conversions'], prv['conversions'])}\n"
            f"👆 *CLICKS:* {cur['clicks']:,}{delta(cur['clicks'], prv['clicks'])}\n"
        )

        report += "\n━━━━━━━━━━━━━━━━━━━━━━\n"

        if top3:
            report += "\n🏆 *TOP PERFORMERS:*\n"
            for i, c in enumerate(top3, 1):
                roas = c.get("purchase_roas", 0)
                conv = c.get("conversions", 0)
                spend = c.get("spend", 0)
                report += f"{i}. {c.get('campaign_name', '?')} — ROAS {roas:.1f} | {conv} conv | {spend:.0f} PLN\n"

        if worst3:
            report += "\n⚠️ *WORST PERFORMERS:*\n"
            for i, c in enumerate(worst3, 1):
                roas = c.get("purchase_roas", 0)
                ctr = c.get("ctr", 0)
                spend = c.get("spend", 0)
                report += f"{i}. {c.get('campaign_name', '?')} — ROAS {roas:.1f} | CTR {ctr:.2f}% | {spend:.0f} PLN\n"

        report += "\n💡 *NEXT WEEK ACTIONS:*\n"
        for rec in recommendations[:3]:
            report += f"• {rec}\n"

        report += f"\n_Raport tygodniowy | {now.strftime('%d.%m.%Y %H:%M')}_"
        return report

    except Exception as e:
        logger.error(f"Błąd generate_weekly_report_dre: {e}")
        return f"❌ Błąd generowania raportu: {str(e)}"


def weekly_report_dre():
    """Wysyła weekly report DRE na C05GPM4E9B8. Piątek 16:00."""
    try:
        dre_channel = os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8")
        logger.info("📊 Generuję Weekly Report DRE...")
        report = generate_weekly_report_dre()
        app.client.chat_postMessage(channel=dre_channel, text=report)
        logger.info("✅ Weekly Report DRE wysłany!")
    except Exception as e:
        logger.error(f"❌ Błąd weekly_report_dre: {e}")


# ============================================
# WEEKLY REPORTS - piątek 16:00
# ============================================

def send_weekly_reports():
    """
    Wysyła tygodniowe raporty performance dla klientów.
    Uruchamiane w piątek o 16:00.
    """
    try:
        warsaw_tz = pytz.timezone('Europe/Warsaw')
        now = datetime.now(warsaw_tz)
        date_to = now.strftime('%Y-%m-%d')
        date_from = (now - timedelta(days=7)).strftime('%Y-%m-%d')
        period = f"{(now - timedelta(days=7)).strftime('%d.%m')} - {now.strftime('%d.%m.%Y')}"

        logger.info(f"📊 Generuję Weekly Reports za {period}...")

        dre_channel = os.environ.get("DRE_CHANNEL_ID")

        # === DRE Weekly Report ===
        if dre_channel:
            meta_data = meta_ads_tool(
                client_name="drzwi dre",
                date_from=date_from, date_to=date_to,
                level="campaign",
                metrics=["campaign_name", "spend", "clicks", "ctr", "cpc",
                         "conversions", "purchase_roas", "impressions", "frequency"]
            )

            google_data = []
            for account in ["dre", "dre 2025"]:
                data = google_ads_tool(
                    client_name=account,
                    date_from=date_from, date_to=date_to,
                    level="campaign",
                    metrics=["campaign.name", "metrics.impressions", "metrics.clicks",
                             "metrics.cost_micros", "metrics.conversions", "metrics.ctr"]
                )
                if data.get("data"):
                    google_data.extend(data["data"])

            all_dre = []
            if meta_data.get("data"):
                all_dre.extend(meta_data["data"])
            all_dre.extend(google_data)

            report = format_weekly_summary("DRE", all_dre, period)
            report += f"\n\n_Raport tygodniowy | {now.strftime('%d.%m.%Y %H:%M')}_"

            app.client.chat_postMessage(channel=dre_channel, text=report)
            logger.info("✅ Weekly Report DRE wysłany!")

    except Exception as e:
        logger.error(f"❌ Błąd send_weekly_reports: {e}")


# ============================================
# TEAM AVAILABILITY SYSTEM
# Pracownicy piszą do Sebola o nieobecnościach,
# Sebol zapisuje i codziennie o 17:00 informuje Daniela
# ============================================

AVAILABILITY_FILE = "/tmp/team_availability.json"

# Szybki pre-filtr (słowa kluczowe PL) zanim wywołamy Claude
ABSENCE_KEYWORDS = [
    "nie będzie", "nie bedzie", "nie ma mnie", "nie będę", "nie bede",
    "urlop", "wolne", "nieobecn", "będę tylko", "bede tylko",
    "będę od", "bede od", "będę do", "bede do",
    "wychodzę wcześniej", "wychodze wczesniej", "wcześniej wychodzę",
    "zdalnie", "home office", "homeoffice", "choruję", "choruje", "l4",
    "nie przyjdę", "nie przyjde", "spóźnię się", "spoznie sie",
    "przyjdę później", "przyjde pozniej", "późniejszy start",
    "tylko rano", "tylko po południu", "tylko popoludniu",
]

def _load_availability():
    """Wczytaj nieobecności z pliku JSON."""
    try:
        if os.path.exists(AVAILABILITY_FILE):
            with open(AVAILABILITY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []

def _save_availability(entries):
    """Zapisz nieobecności do pliku JSON, czyść starsze niż 60 dni."""
    try:
        cutoff = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
        entries = [e for e in entries if e.get("date", "2000-01-01") >= cutoff]
        with open(AVAILABILITY_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ Błąd zapisu availability: {e}")

def _parse_availability_with_claude(user_message, user_name):
    """
    Użyj Claude do sparsowania wiadomości o nieobecności.
    Zwraca listę {date, type, details} lub None.
    """
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_weekday = datetime.now().strftime('%A')

    prompt = f"""Analizujesz wiadomość od pracownika polskiej agencji o jego dostępności.

Dzisiaj: {today_str} ({today_weekday})
Wiadomość od {user_name}: "{user_message}"

Jeśli to wiadomość o nieobecności lub ograniczonej dostępności, wyciągnij info.
Typy nieobecności:
- "absent" = cały dzień nieobecny/a
- "morning_only" = tylko rano (do ~12:00)
- "afternoon_only" = tylko po południu (od ~12:00)
- "late_start" = późniejszy start (np. od 10-11:00)
- "early_end" = wcześniejsze wyjście
- "remote" = praca zdalna (dostępny/a, inna lokalizacja)
- "partial" = częściowo dostępny/a

Daty: "jutro"=następny dzień, "pojutrze"=za 2 dni, "w piątek"=ten tydzień itp.
Może być wiele dat (np. "wtorek i środa").

Odpowiedz TYLKO JSON:
{{
  "is_availability": true/false,
  "entries": [
    {{"date": "YYYY-MM-DD", "type": "absent", "details": "opis po polsku, np. urlop"}}
  ]
}}
Jeśli to nie wiadomość o dostępności: {{"is_availability": false, "entries": []}}"""

    try:
        resp = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        import re as _re
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
        # Usuń poprzedni wpis tego usera na ten sam dzień
        all_entries = [e for e in all_entries
                       if not (e["user_id"] == user_id and e["date"] == entry["date"])]
        all_entries.append({
            "user_id": user_id,
            "user_name": user_name,
            "date": entry["date"],
            "type": entry["type"],
            "details": entry.get("details", ""),
            "recorded_at": datetime.now().isoformat(),
        })
        saved_dates.append(entry["date"])
    _save_availability(all_entries)
    return saved_dates

def get_availability_for_date(target_date):
    """Zwraca listę nieobecności na dany dzień."""
    return [e for e in _load_availability() if e.get("date") == target_date]

def _next_workday(from_date=None):
    """Zwraca następny dzień roboczy (pomiń weekend)."""
    d = from_date or datetime.now()
    d = d + timedelta(days=1)
    while d.weekday() >= 5:  # sob=5, nie=6
        d = d + timedelta(days=1)
    return d

def _format_availability_summary(entries, date_label):
    """Formatuje czytelne podsumowanie dla Daniela."""
    TYPE_LABELS = {
        "absent":           "❌ Nieobecna/y cały dzień",
        "morning_only":     "🌅 Tylko rano",
        "afternoon_only":   "🌆 Tylko po południu",
        "late_start":       "🕙 Późniejszy start",
        "early_end":        "🏃 Wcześniejsze wyjście",
        "remote":           "🏠 Praca zdalna",
        "partial":          "⏰ Częściowo dostępna/y",
    }
    if not entries:
        return f"✅ *{date_label}* — wszyscy w biurze, żadnych nieobecności 🎉"

    msg = f"📅 *Dostępność teamu — {date_label}:*\n\n"
    for e in entries:
        type_label = TYPE_LABELS.get(e["type"], "⚠️ Ograniczona dostępność")
        msg += f"• *{e['user_name']}* — {type_label}\n"
        if e.get("details"):
            msg += f"  _{e['details']}_\n"
    return msg

def send_daily_team_availability():
    """Wysyła Danielowi o 17:00: dostępność jutro + otwarte prośby teamu."""
    try:
        tomorrow = _next_workday()
        tomorrow_str = tomorrow.strftime('%Y-%m-%d')
        tomorrow_label = tomorrow.strftime('%A %d.%m.%Y')

        # --- Sekcja 1: Nieobecności jutro ---
        abs_entries = get_availability_for_date(tomorrow_str)
        abs_msg = _format_availability_summary(abs_entries, tomorrow_label)

        # --- Sekcja 2: Otwarte prośby ---
        pending = get_pending_requests()
        if pending:
            req_msg = f"\n\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            req_msg += _format_requests_list(pending)
        else:
            req_msg = "\n\n━━━━━━━━━━━━━━━━━━━━━━\n\n✅ Brak otwartych próśb."

        full_msg = abs_msg + req_msg
        # Wysyłaj na kanał #zarzondpato
        app.client.chat_postMessage(channel="C0AJ4HBS94G", text=full_msg)
        logger.info(f"✅ Team summary wysłane na #zarzondpato (nieobecności: {len(abs_entries)}, prośby: {len(pending)})")
    except Exception as e:
        logger.error(f"❌ Błąd send_daily_team_availability: {e}")

# ============================================
# TEAM REQUESTS SYSTEM
# Prośby pracowników które trafiają do Daniela
# i zostają otwarte dopóki nie zostaną zamknięte
# ============================================

REQUESTS_FILE = "/tmp/team_requests.json"

REQUEST_CATEGORY_LABELS = {
    "urlop":     "🏖️ Urlop / czas wolny",
    "zakup":     "🛒 Zakup / sprzęt",
    "dostep":    "🔑 Dostęp / narzędzia",
    "spotkanie": "📆 Spotkanie / rozmowa",
    "problem":   "⚠️ Problem / zgłoszenie",
    "pytanie":   "❓ Pytanie / decyzja",
    "inne":      "📌 Inne",
}

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
    req_id = _next_request_id()
    requests.append({
        "id": req_id,
        "user_id": user_id,
        "user_name": user_name,
        "category": category,
        "summary": summary,
        "original_message": original_message,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "closed_at": None,
    })
    _save_requests(requests)
    return req_id

def close_request(req_id):
    """Zamknij prośbę po ID. Zwraca dict prośby lub None jeśli nie znaleziono."""
    requests = _load_requests()
    found = None
    for r in requests:
        if r.get("id") == req_id and r.get("status") == "pending":
            r["status"] = "done"
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
        created = datetime.fromisoformat(r["created_at"]).strftime('%d.%m %H:%M')
        msg += f"*#{r['id']}* — *{r['user_name']}* [{created}]\n"
        msg += f"  {cat_label}: {r['summary']}\n\n"
    msg += "_Zamknij: `@Sebol zamknij #N`_"
    return msg


# ============================================
# UNIFIED EMPLOYEE DM HANDLER
# Jeden Claude call → klasyfikuje: nieobecność / prośba / zwykła rozmowa
# ============================================

# Pre-filtr — czy wiadomość W OGÓLE może być nieobecnością lub prośbą?
# Jeśli nie pasuje żaden keyword → od razu leci do zwykłego Claude chat
EMPLOYEE_MSG_KEYWORDS = ABSENCE_KEYWORDS + [
    "prośba", "prosba", "chciał", "chcialbym", "chciałabym", "chciałem",
    "czy mogę", "czy moge", "czy możemy", "czy mozemy", "czy możesz",
    "potrzebuję", "potrzebuje", "potrzebna", "potrzebny",
    "chcę", "chce", "wnioskuję", "wniosek",
    "urlop", "wolne", "zakup", "zamówić", "zamowic",
    "dostęp", "dostep", "konto", "licencja",
    "spotkanie", "porozmawiać", "porozmawiac", "umówić", "umowic",
    "problem", "błąd", "blad", "nie działa", "nie dziala",
    "pytanie", "zapytać", "zapytac", "decyzja",
    "podwyżka", "podwyzka", "nadgodziny", "nadgodzin",
    "faktura", "rachunek", "rozliczenie",
]

def handle_employee_dm(user_id, user_name, user_message, say):
    """
    Główny handler DM od pracownika.
    STAGE 1: jeśli pasuje ABSENCE_KEYWORDS → od razu parsuj jako nieobecność (bez pytania Claude co to jest)
    STAGE 2: jeśli pasuje EMPLOYEE_MSG_KEYWORDS → zapytaj Claude czy to prośba
    Zwraca True jeśli obsłużono, False = zwykła rozmowa → idzie do normalnego Claude chat
    """
    msg_lower = user_message.lower()

    # ── STAGE 1: ABSENCE — twarde słowa kluczowe, nie pytamy Claude czy to chat ──
    if any(kw in msg_lower for kw in ABSENCE_KEYWORDS):
        entries = _parse_availability_with_claude(user_message, user_name)
        if entries:
            saved_dates = save_availability_entry(user_id, user_name, entries)
            if saved_dates:
                if len(saved_dates) == 1:
                    date_fmt = datetime.strptime(saved_dates[0], '%Y-%m-%d').strftime('%A %d.%m')
                    say(f"✅ Zapisałem! *{date_fmt}* — Daniel dostanie info dziś o 17:00. 👍")
                else:
                    dates_fmt = ", ".join(datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m') for d in saved_dates)
                    say(f"✅ Zapisałem nieobecności: *{dates_fmt}* — Daniel dostanie info o 17:00. 👍")
                logger.info(f"📅 Availability: {user_name} → {saved_dates}")
                return True

    # ── STAGE 2: REQUEST — słowa kluczowe próśb, pytamy Claude o klasyfikację ──
    request_keywords = [kw for kw in EMPLOYEE_MSG_KEYWORDS if kw not in ABSENCE_KEYWORDS]
    if not any(kw in msg_lower for kw in request_keywords):
        return False

    today_str = datetime.now().strftime('%Y-%m-%d')
    today_weekday = datetime.now().strftime('%A')

    prompt = f"""Jesteś asystentem w polskiej agencji marketingowej.
Pracownik {user_name} napisał do bota Slack.

Dzisiaj: {today_str} ({today_weekday})
Wiadomość: "{user_message}"

Czy to prośba do szefa której nie możesz sam obsłużyć?
Przykłady próśb: urlop do zatwierdzenia, zakup sprzętu, dostęp do narzędzi, prośba o spotkanie, pytanie o decyzję, problem do rozwiązania, podwyżka.

Odpowiedz TYLKO JSON:
{{
  "is_request": true/false,
  "request_category": "urlop|zakup|dostep|spotkanie|problem|pytanie|inne",
  "request_summary": "Krótki opis prośby po polsku (max 1 zdanie)"
}}"""

    try:
        resp = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        import re as _re
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not m:
            return False
        data = json.loads(m.group())

        if data.get("is_request"):
            category = data.get("request_category", "inne")
            summary = data.get("request_summary", user_message[:100])
            req_id = save_request(user_id, user_name, category, summary, user_message)
            cat_label = REQUEST_CATEGORY_LABELS.get(category, "📌 Inne")
            say(f"✅ Zapisałem Twoją prośbę *#{req_id}* — {cat_label}\n"
                f"_{summary}_\n\n"
                f"Daniel dostanie info dziś o 17:00. Jak tylko odpowie, wróci do Ciebie bezpośrednio. 👍")
            logger.info(f"📋 Request #{req_id}: {user_name} → {category}: {summary}")
            return True

        return False  # nie prośba → idzie do normalnego Claude chat

    except Exception as e:
        logger.error(f"❌ Błąd handle_employee_dm: {e}")
        return False


# ============================================
# DAILY EMAIL SUMMARY → Slack DM
# ============================================

def daily_email_summary_slack():
    """
    Czyta emaile z daniel@patoagencja.com, kategoryzuje przez Claude,
    wysyła podsumowanie jako Slack DM do Daniela (UTE1RN6SJ) o 16:00.
    """
    daniel_user_id = "UTE1RN6SJ"
    today_str = datetime.now().strftime('%d.%m.%Y')
    today_date = datetime.now().date()

    try:
        logger.info("📧 Generuję Daily Email Summary...")

        # 1. Pobierz emaile
        result = email_tool(user_id=daniel_user_id, action="read", limit=50, folder="INBOX")

        if "error" in result:
            app.client.chat_postMessage(
                channel=daniel_user_id,
                text=f"📧 **Email Summary - {today_str}**\n\n❌ Nie udało się pobrać emaili: {result['error']}"
            )
            return

        all_emails = result.get("emails", [])

        # 2. Filtruj: dzisiejsze + ostatnie 3 dni (dla unreplied check)
        from email.utils import parsedate_to_datetime
        cutoff_date = (datetime.now() - timedelta(days=3)).date()
        today_emails_raw = []
        recent_emails = []
        for em in all_emails:
            try:
                em_date = parsedate_to_datetime(em["date"]).date()
                if em_date == today_date:
                    today_emails_raw.append(em)
                elif em_date >= cutoff_date:
                    recent_emails.append(em)
            except Exception:
                pass

        # 2b. Pre-filtruj newslettery (mają List-Unsubscribe/List-Id itp.)
        today_emails = [e for e in today_emails_raw if not e.get("is_newsletter")]
        newsletter_count = len(today_emails_raw) - len(today_emails)

        # 3. Sprawdź unreplied — tylko non-newsletter z ostatnich 3 dni
        email_config = get_user_email_config(daniel_user_id)
        all_recent = today_emails + [e for e in recent_emails if not e.get("is_newsletter")]
        unreplied = find_unreplied_emails(email_config, all_recent, days_back=3) if email_config else []
        unreplied_map = {_normalize_subject(e['subject']): e for e in unreplied}

        # 4. Edge case: brak ważnych emaili dzisiaj
        if not today_emails:
            no_email_msg = f"📧 *Email Summary - {today_str}*\n\n✅ Brak nowych ważnych emaili dzisiaj."
            if newsletter_count:
                no_email_msg += f"\n_(pominięto {newsletter_count} newsletterów/mailingów)_"
            if unreplied:
                no_email_msg += f"\n\n🚨 *UWAGA: {len(unreplied)} emaili bez odpowiedzi z ostatnich 3 dni!*\n"
                for em in unreplied[:5]:
                    days = em.get('days_waiting', '?')
                    no_email_msg += f"  • *{em['subject']}* — od: {em['from']} _(czeka {days}d)_\n"
            app.client.chat_postMessage(channel=daniel_user_id, text=no_email_msg)
            logger.info("✅ Email Summary wysłany (brak ważnych emaili).")
            return

        # 5. Kategoryzuj przez Claude — tylko pre-filtrowane emaile
        emails_for_claude = "\n\n".join([
            f"Email {i+1}:\nOd: {e['from']}\nTemat: {e['subject']}\nPodgląd: {e['body_preview']}"
            for i, e in enumerate(today_emails)
        ])

        claude_prompt = f"""Kategoryzujesz emaile dla Daniela Koszuka, właściciela agencji marketingowej Pato.

Newslettery i mailingi masowe zostały już odfiltrowane — te {len(today_emails)} emaili to potencjalnie ważna korespondencja.

Dla każdego emaila przypisz kategorię:
- IMPORTANT: bezpośrednia korespondencja od klienta/partnera/dostawcy, faktura, oferta, pytanie wymagające odpowiedzi Daniela
- ADMIN: automatyczne potwierdzenia, powiadomienia systemowe, raporty cykliczne — NIE wymagają odpowiedzi
- SPAM: niechciane, nieistotne

ZASADA: Oznacz IMPORTANT TYLKO jeśli Daniel MUSI coś z tym zrobić. Automatyczne powiadomienia i potwierdzenia = ADMIN.

Dla każdego IMPORTANT: napisz 1-2 zdania po polsku co chce nadawca i jakiej akcji wymaga.
Zaproponuj max 3 konkretne sugerowane akcje tylko dla IMPORTANT emaili.

Emaile:
{emails_for_claude}

Odpowiedz TYLKO w formacie JSON:
{{
  "categorized": [
    {{"index": 0, "category": "IMPORTANT", "summary": "Klient pyta o wycenę kampanii Q2. Wymaga odpowiedzi.", "from": "...", "subject": "..."}},
    {{"index": 1, "category": "ADMIN", "summary": null, "from": "...", "subject": "..."}}
  ],
  "suggested_actions": ["Odpowiedz na email od X ws. wyceny"]
}}"""

        # Retry logic dla 529 Overloaded
        import time as _time
        claude_response = None
        for _attempt in range(3):
            try:
                claude_response = anthropic.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1500,
                    messages=[{"role": "user", "content": claude_prompt}]
                )
                break
            except Exception as _api_err:
                err_str = str(_api_err)
                if _attempt < 2 and ("529" in err_str or "overloaded" in err_str.lower() or "529" in err_str):
                    _wait = 40 * (2 ** _attempt)  # 40s, 80s
                    logger.warning(f"⚠️ Claude API overloaded (próba {_attempt+1}/3) — czekam {_wait}s... ({_api_err})")
                    _time.sleep(_wait)
                else:
                    raise
        if claude_response is None:
            raise Exception("Claude API niedostępne po 3 próbach")

        # Parse JSON z odpowiedzi Claude
        import re
        raw_text = claude_response.content[0].text
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        categorized_data = json.loads(json_match.group()) if json_match else {"categorized": [], "suggested_actions": []}

        categorized = categorized_data.get("categorized", [])
        suggested_actions = categorized_data.get("suggested_actions", [])

        # 6. Zlicz kategorie
        important = [c for c in categorized if c.get("category") == "IMPORTANT"]
        marketing = [c for c in categorized if c.get("category") == "MARKETING"]
        admin = [c for c in categorized if c.get("category") == "ADMIN"]
        spam = [c for c in categorized if c.get("category") == "SPAM"]

        # Oznacz które IMPORTANT nie mają odpowiedzi
        for em in important:
            subj = _normalize_subject(em.get("subject", ""))
            if subj in unreplied_map:
                em["unreplied"] = True
                em["days_waiting"] = unreplied_map[subj].get("days_waiting", 0)

        # 7. Zbuduj wiadomość Slack
        msg = f"📧 *Email Summary - {today_str}*\n\n"
        msg += f"📥 *OTRZYMANE DZISIAJ:* {len(today_emails)} ważnych emaili"
        if newsletter_count:
            msg += f" _(+ {newsletter_count} newsletterów pominięto)_"
        msg += "\n"

        # Sekcja URGENT (bez odpowiedzi z poprzednich dni)
        old_unreplied = [e for e in unreplied if e.get('days_waiting', 0) > 0]
        if old_unreplied:
            msg += "\n━━━━━━━━━━━━━━━━━━━━━━\n"
            msg += f"\n🚨 *URGENT - BEZ ODPOWIEDZI (ostatnie 3 dni):*\n\n"
            for em in old_unreplied[:5]:
                days = em.get('days_waiting', '?')
                msg += f"⏰ *{em['subject']}*\n"
                msg += f"   Od: {em['from']} | Czeka: *{days} {'dzień' if days == 1 else 'dni'}*\n\n"

        msg += "\n━━━━━━━━━━━━━━━━━━━━━━\n"

        if important:
            msg += f"\n🔴 *WAŻNE (wymagają odpowiedzi):*\n\n"
            for i, em in enumerate(important, 1):
                idx = em.get("index", 0)
                raw = today_emails[idx] if idx < len(today_emails) else {}
                sender = em.get("from", raw.get("from", "?"))
                subject = em.get("subject", raw.get("subject", "?"))
                summary = em.get("summary") or ""
                unreplied_flag = " ⏰ *brak odpowiedzi*" if em.get("unreplied") else ""
                msg += f"{i}. *Od:* {sender}{unreplied_flag}\n"
                msg += f"   *Temat:* {subject}\n"
                if summary:
                    msg += f"   *Podsumowanie:* {summary}\n"
                msg += "\n"
        else:
            msg += "\n✅ *Brak ważnych emaili dzisiaj*\n"

        msg += "━━━━━━━━━━━━━━━━━━━━━━\n"
        msg += "\n📊 *POZOSTAŁE:*\n"
        if marketing:
            msg += f"- Marketing/newslettery: {len(marketing)} emaili\n"
        if admin:
            msg += f"- Faktury/admin: {len(admin)} emaili\n"
        if spam:
            msg += f"- Spam/low priority: {len(spam)} emaili\n"

        if suggested_actions:
            msg += "\n━━━━━━━━━━━━━━━━━━━━━━\n"
            msg += "\n💡 *SUGEROWANE AKCJE:*\n"
            for action in suggested_actions[:3]:
                msg += f"- {action}\n"

        msg += "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        msg += "Miłego wieczoru! 🌆"

        # 7. Wyślij DM
        app.client.chat_postMessage(
            channel=daniel_user_id,
            text=msg
        )
        logger.info(f"✅ Email Summary wysłany! ({len(today_emails)} emaili, {len(important)} ważnych)")

    except Exception as e:
        logger.error(f"❌ Błąd daily_email_summary_slack: {e}")
        try:
            app.client.chat_postMessage(
                channel=daniel_user_id,
                text=f"📧 **Email Summary - {today_str}**\n\n❌ Błąd generowania podsumowania: {str(e)}"
            )
        except Exception:
            pass


# Scheduler
scheduler = BackgroundScheduler(timezone=pytz.timezone('Europe/Warsaw'))
scheduler.add_job(daily_summaries, 'cron', hour=16, minute=0)
scheduler.add_job(daily_digest_dre, 'cron', hour=9, minute=0, id='daily_digest_dre')
scheduler.add_job(weekly_checkin, 'cron', day_of_week='fri', hour=14, minute=0)
scheduler.add_job(checkin_summary, 'cron', day_of_week='mon', hour=9, minute=0)
scheduler.add_job(check_budget_alerts, 'cron', minute=0, id='budget_alerts')
scheduler.add_job(send_budget_alerts_dre, 'cron', hour='9,11,13,15,17,19', minute=0, id='budget_alerts_dre')
scheduler.add_job(weekly_report_dre, 'cron', day_of_week='fri', hour=16, minute=0, id='weekly_reports')
scheduler.add_job(weekly_learnings_dre, 'cron', day_of_week='mon,thu', hour=8, minute=30, id='weekly_learnings')
scheduler.add_job(daily_email_summary_slack, 'cron', hour=16, minute=0, id='daily_email_summary')
# Team availability: podsumowanie jutrzejszej dostępności, pn-pt o 17:00
scheduler.add_job(send_daily_team_availability, 'cron', day_of_week='mon-fri', hour=17, minute=0, id='team_availability')
scheduler.start()

print(f"✅ Scheduler załadowany! Jobs: {len(scheduler.get_jobs())}")
print("✅ Scheduler wystartował!")

# Uruchom bota
handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
print("⚡️ Bot działa!")
handler.start()
