"""Meta Ads API tools — no Slack app dependency."""
import os
import json
import logging
from datetime import datetime, timedelta

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount

logger = logging.getLogger(__name__)

try:
    FacebookAdsApi.init(access_token=os.environ.get("META_ACCESS_TOKEN"))
    _meta_initialized = True
except Exception as _e:
    logger.error(f"Błąd inicjalizacji Meta Ads API: {_e}")
    _meta_initialized = False


def parse_relative_date(date_string):
    """Konwertuj względne daty na YYYY-MM-DD"""
    if not date_string:
        return None

    if len(date_string) == 10 and date_string[4] == '-' and date_string[7] == '-':
        return date_string

    today = datetime.now()
    date_lower = date_string.lower()

    if 'dzisiaj' in date_lower or 'today' in date_lower:
        return today.strftime('%Y-%m-%d')

    if 'wczoraj' in date_lower or 'yesterday' in date_lower:
        return (today - timedelta(days=1)).strftime('%Y-%m-%d')
    elif 'tydzień' in date_lower or 'week' in date_lower:
        if 'ostatni' in date_lower or 'last' in date_lower:
            return (today - timedelta(days=7)).strftime('%Y-%m-%d')
    elif 'miesiąc' in date_lower or 'month' in date_lower:
        return (today - timedelta(days=30)).strftime('%Y-%m-%d')

    import re
    match = re.search(r'(\d+)\s*(dzień|dni|day|days)', date_lower)
    if match:
        return (today - timedelta(days=int(match.group(1)))).strftime('%Y-%m-%d')

    months = {
        'styczeń': 1, 'stycznia': 1, 'luty': 2, 'lutego': 2,
        'marzec': 3, 'marca': 3, 'kwiecień': 4, 'kwietnia': 4,
        'maj': 5, 'maja': 5, 'czerwiec': 6, 'czerwca': 6,
        'lipiec': 7, 'lipca': 7, 'sierpień': 8, 'sierpnia': 8,
        'wrzesień': 9, 'września': 9, 'październik': 10, 'października': 10,
        'listopad': 11, 'listopada': 11, 'grudzień': 12, 'grudnia': 12,
    }
    for month_name, month_num in months.items():
        if month_name in date_lower:
            year_match = re.search(r'202[0-9]', date_string)
            if year_match:
                from datetime import datetime as _dt
                date_obj = _dt(int(year_match.group()), month_num, 1)
                return date_obj.strftime('%Y-%m-%d')

    return date_string


def meta_ads_tool(date_from=None, date_to=None, level="campaign", campaign_name=None,
                  adset_name=None, ad_name=None, metrics=None, breakdown=None,
                  limit=None, client_name=None):
    """Pobiera dane z Meta Ads API na różnych poziomach dla różnych klientów."""
    # Wspieramy oba env vary: META_AD_ACCOUNTS i META_AD_ACCOUNT_ID (fallback)
    accounts_json = os.environ.get("META_AD_ACCOUNTS") or os.environ.get("META_AD_ACCOUNT_ID", "{}")
    try:
        accounts_map = json.loads(accounts_json)
    except json.JSONDecodeError:
        accounts_map = {}

    if not client_name:
        return {
            "message": "Nie podano nazwy klienta. Dostępne klienty:",
            "available_clients": list(set(accounts_map.keys())),
            "hint": "Podaj nazwę klienta w zapytaniu",
        }

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
            "hint": "Sprawdź pisownię lub wybierz z dostępnych klientów",
        }

    try:
        if date_from:
            date_from = parse_relative_date(date_from)
        if date_to:
            date_to = parse_relative_date(date_to)

        # Walidacja roku
        for _d in [date_from, date_to]:
            pass
        if date_from and len(date_from) >= 4 and int(date_from[:4]) < 2026:
            date_from = '2026' + date_from[4:]
        if date_to and len(date_to) >= 4 and int(date_to[:4]) < 2026:
            date_to = '2026' + date_to[4:]

        if not date_to:
            date_to = datetime.now().strftime('%Y-%m-%d')
        if not date_from:
            date_from = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        account = AdAccount(ad_account_id)

        available_metrics = {
            'campaign': ['campaign_name', 'spend', 'impressions', 'clicks', 'ctr', 'cpc',
                         'cpm', 'reach', 'frequency', 'conversions', 'cost_per_conversion',
                         'purchase_roas', 'actions', 'action_values'],
            'adset':    ['campaign_name', 'adset_name', 'spend', 'impressions', 'clicks',
                         'ctr', 'cpc', 'cpm', 'reach', 'conversions', 'cost_per_conversion'],
            'ad':       ['campaign_name', 'adset_name', 'ad_name', 'spend', 'impressions',
                         'clicks', 'ctr', 'cpc', 'cpm', 'reach', 'conversions',
                         'inline_link_clicks', 'inline_link_click_ctr'],
        }

        if not metrics:
            metrics = available_metrics.get(level, available_metrics['campaign'])

        params = {
            'time_range': {'since': date_from, 'until': date_to},
            'level': level,
            'fields': metrics,
        }
        if breakdown:
            params['breakdowns'] = [breakdown] if isinstance(breakdown, str) else breakdown
        if limit:
            params['limit'] = limit

        insights = account.get_insights(params=params)
        if not insights:
            return {"message": f"Brak danych za okres {date_from} - {date_to} na poziomie {level}"}

        data = []
        for insight in insights:
            item = {}
            for metric in metrics:
                value = insight.get(metric)
                if value is not None:
                    if metric in ['spend', 'cpc', 'cpm', 'ctr', 'frequency',
                                  'cost_per_conversion', 'purchase_roas',
                                  'inline_link_click_ctr']:
                        item[metric] = float(value)
                    elif metric in ['impressions', 'clicks', 'reach',
                                    'conversions', 'inline_link_clicks']:
                        item[metric] = int(value)
                    elif metric in ['actions', 'action_values']:
                        item[metric] = value
                    else:
                        item[metric] = str(value)

            if breakdown:
                breakdown_list = [breakdown] if isinstance(breakdown, str) else breakdown
                for b in breakdown_list:
                    if b in insight:
                        item[b] = insight[b]

            skip = False
            if campaign_name and 'campaign_name' in item:
                if campaign_name.lower() not in item['campaign_name'].lower():
                    skip = True
            if adset_name and 'adset_name' in item:
                if adset_name.lower() not in item['adset_name'].lower():
                    skip = True
            if ad_name and 'ad_name' in item:
                if ad_name.lower() not in item['ad_name'].lower():
                    skip = True
            if not skip:
                data.append(item)

        return {
            "date_from": date_from,
            "date_to":   date_to,
            "level":     level,
            "breakdown": breakdown,
            "total_items": len(data),
            "data": data,
        }

    except Exception as e:
        logger.error(f"Błąd pobierania danych Meta Ads: {e}")
        return {"error": str(e)}
