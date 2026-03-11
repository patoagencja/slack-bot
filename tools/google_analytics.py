"""Google Analytics 4 Data API — no Slack app dependency."""
import os
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

try:
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Metric, RunReportRequest,
    )
    from google.oauth2 import service_account

    _creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    _creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")

    if _creds_json:
        import tempfile, json as _json
        _tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        _tmp.write(_creds_json)
        _tmp.flush()
        _ga4_client = BetaAnalyticsDataClient.from_service_account_file(_tmp.name)
    elif _creds_path:
        _ga4_client = BetaAnalyticsDataClient.from_service_account_file(_creds_path)
    else:
        _ga4_client = None

    if _ga4_client:
        logger.info("✅ Google Analytics 4 API zainicjalizowane")
    else:
        logger.warning("⚠️  GA4: brak GOOGLE_APPLICATION_CREDENTIALS — tool wyłączony")
except Exception as _e:
    logger.error(f"Błąd inicjalizacji GA4 API: {_e}")
    _ga4_client = None


DEFAULT_DIMENSIONS = ["sessionDefaultChannelGroup", "sessionSourceMedium"]
DEFAULT_METRICS = [
    "sessions", "totalUsers", "newUsers",
    "screenPageViews", "bounceRate",
    "conversions", "totalRevenue",
    "averageSessionDuration",
]


def google_analytics_tool(
    client_name: str = None,
    date_from: str = None,
    date_to: str = None,
    dimensions: list = None,
    metrics: list = None,
    limit: int = 20,
):
    """Pobiera dane z Google Analytics 4 dla danego klienta."""
    if not _ga4_client:
        return {"error": "GA4 API nie jest skonfigurowane. Ustaw GOOGLE_APPLICATION_CREDENTIALS lub GOOGLE_APPLICATION_CREDENTIALS_JSON."}

    properties_json = os.environ.get("GA4_PROPERTY_IDS", "{}")
    try:
        properties_map = json.loads(properties_json)
    except json.JSONDecodeError:
        properties_map = {}

    if not client_name:
        return {
            "message": "Nie podano nazwy klienta. Dostępne klienty:",
            "available_clients": list(properties_map.keys()),
            "hint": "Podaj nazwę klienta w zapytaniu",
        }

    client_lower = client_name.lower()
    property_id = None
    for key, value in properties_map.items():
        if key.lower() == client_lower or client_lower in key.lower():
            property_id = value
            break

    if not property_id:
        return {
            "error": f"Nie znaleziono property GA4 dla klienta '{client_name}'",
            "available_clients": list(properties_map.keys()),
            "hint": "Sprawdź pisownię lub dodaj klienta do GA4_PROPERTY_IDS",
        }

    # Daty
    if not date_to:
        date_to = "today"
    if not date_from:
        date_from = "7daysAgo"

    # Normalizacja dat — API GA4 akceptuje YYYY-MM-DD lub 'today'/'NdaysAgo'
    def _normalize(d):
        if not d:
            return d
        for label, delta in [("dzisiaj", 0), ("wczoraj", 1), ("ostatni tydzień", 7), ("ostatni miesiąc", 30)]:
            if label in d.lower():
                if delta == 0:
                    return "today"
                return f"{delta}daysAgo"
        return d

    date_from = _normalize(date_from)
    date_to = _normalize(date_to)

    dims = [Dimension(name=d) for d in (dimensions or DEFAULT_DIMENSIONS)]
    mets = [Metric(name=m) for m in (metrics or DEFAULT_METRICS)]

    try:
        request = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=dims,
            metrics=mets,
            date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
            limit=limit,
        )
        response = _ga4_client.run_report(request)

        dim_headers = [h.name for h in response.dimension_headers]
        met_headers = [h.name for h in response.metric_headers]

        rows = []
        for row in response.rows:
            item = {}
            for i, val in enumerate(row.dimension_values):
                item[dim_headers[i]] = val.value
            for i, val in enumerate(row.metric_values):
                try:
                    item[met_headers[i]] = float(val.value)
                except ValueError:
                    item[met_headers[i]] = val.value
            rows.append(item)

        return {
            "client": client_name,
            "property_id": property_id,
            "date_from": date_from,
            "date_to": date_to,
            "dimensions": dim_headers,
            "metrics": met_headers,
            "total_rows": len(rows),
            "data": rows,
        }

    except Exception as e:
        logger.error(f"Błąd pobierania danych GA4: {e}")
        return {"error": str(e)}
