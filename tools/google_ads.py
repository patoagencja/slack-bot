"""Google Ads API tools — no Slack app dependency."""
import os
import json
import logging
from datetime import datetime, timedelta

from google.ads.googleads.client import GoogleAdsClient

logger = logging.getLogger(__name__)

try:
    _config = {
        'developer_token':   os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN"),
        'client_id':         os.environ.get("GOOGLE_ADS_CLIENT_ID"),
        'client_secret':     os.environ.get("GOOGLE_ADS_CLIENT_SECRET"),
        'refresh_token':     os.environ.get("GOOGLE_ADS_REFRESH_TOKEN"),
        'login_customer_id': os.environ.get('GOOGLE_ADS_LOGIN_CUSTOMER_ID', '1355353554'),
        'use_proto_plus':    True,
    }
    google_ads_client = GoogleAdsClient.load_from_dict(_config)
    logger.info("✅ Google Ads API zainicjalizowane")
except Exception as _e:
    logger.error(f"Błąd inicjalizacji Google Ads API: {_e}")
    google_ads_client = None


def _parse_relative_date(date_string):
    """Konwertuj względne daty na YYYY-MM-DD (local copy without circular import)."""
    from tools.meta_ads import parse_relative_date
    return parse_relative_date(date_string)


def google_ads_tool(date_from=None, date_to=None, level="campaign", campaign_name=None,
                    adgroup_name=None, ad_name=None, metrics=None, limit=None,
                    client_name=None):
    """Pobiera dane z Google Ads API na różnych poziomach dla różnych klientów."""
    if not google_ads_client:
        return {"error": "Google Ads API nie jest skonfigurowane."}

    accounts_json = os.environ.get("GOOGLE_ADS_CUSTOMER_IDS", "{}")
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
    customer_id = None
    for key, value in accounts_map.items():
        if key.lower() == client_name_lower or client_name_lower in key.lower():
            customer_id = value
            break

    if not customer_id:
        return {
            "error": f"Nie znaleziono konta dla klienta '{client_name}'",
            "available_clients": list(set(accounts_map.keys())),
            "hint": "Sprawdź pisownię",
        }

    try:
        if date_from:
            date_from = _parse_relative_date(date_from)
        if date_to:
            date_to = _parse_relative_date(date_to)

        if date_from and len(date_from) >= 4 and int(date_from[:4]) < 2026:
            date_from = '2026' + date_from[4:]
        if date_to and len(date_to) >= 4 and int(date_to[:4]) < 2026:
            date_to = '2026' + date_to[4:]

        if not date_to:
            date_to = datetime.now().strftime('%Y-%m-%d')
        if not date_from:
            date_from = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

        date_from_ga = date_from.replace('-', '')
        date_to_ga   = date_to.replace('-', '')

        default_metrics = {
            'campaign': ['campaign.name', 'metrics.impressions', 'metrics.clicks',
                         'metrics.cost_micros', 'metrics.conversions', 'metrics.ctr',
                         'metrics.average_cpc'],
            'adgroup':  ['campaign.name', 'ad_group.name', 'metrics.impressions',
                         'metrics.clicks', 'metrics.cost_micros', 'metrics.conversions',
                         'metrics.ctr'],
            'ad':       ['campaign.name', 'ad_group.name', 'ad_group_ad.ad.name',
                         'metrics.impressions', 'metrics.clicks', 'metrics.cost_micros',
                         'metrics.ctr'],
        }
        if not metrics:
            metrics = default_metrics.get(level, default_metrics['campaign'])

        resource_map = {'campaign': 'campaign', 'adgroup': 'ad_group', 'ad': 'ad_group_ad'}
        resource = resource_map.get(level, 'campaign')
        fields = ', '.join(metrics)

        query = (f"SELECT {fields} FROM {resource} "
                 f"WHERE segments.date BETWEEN '{date_from_ga}' AND '{date_to_ga}'")
        if campaign_name:
            query += f" AND campaign.name LIKE '%{campaign_name}%'"
        if adgroup_name and level in ['adgroup', 'ad']:
            query += f" AND ad_group.name LIKE '%{adgroup_name}%'"
        if limit:
            query += f" LIMIT {limit}"

        ga_service = google_ads_client.get_service("GoogleAdsService")
        response = ga_service.search(customer_id=customer_id, query=query)

        data = []
        for row in response:
            item = {}
            for metric in metrics:
                parts = metric.split('.')
                value = row
                try:
                    for part in parts:
                        value = getattr(value, part)
                    if 'cost_micros' in metric:
                        item['cost'] = float(value) / 1_000_000
                    elif 'ctr' in metric or 'cpc' in metric:
                        item[parts[-1]] = float(value)
                    elif isinstance(value, (int, float)):
                        item[parts[-1]] = value
                    else:
                        item[parts[-1]] = str(value)
                except Exception:
                    pass

            skip = False
            if campaign_name and 'name' in item:
                if campaign_name.lower() not in str(item.get('name', '')).lower():
                    skip = True
            if not skip:
                data.append(item)

        return {
            "date_from":   date_from,
            "date_to":     date_to,
            "level":       level,
            "customer_id": customer_id,
            "total_items": len(data),
            "data": data,
        }

    except Exception as e:
        logger.error(f"Błąd pobierania danych Google Ads: {e}")
        return {"error": str(e)}
