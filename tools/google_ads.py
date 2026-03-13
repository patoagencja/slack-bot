"""Google Ads API tools — no Slack app dependency."""
import os
import json
import re
import logging
from datetime import datetime, timedelta

try:
    from google.ads.googleads.client import GoogleAdsClient
    _GOOGLE_ADS_AVAILABLE = True
except ImportError:
    GoogleAdsClient = None
    _GOOGLE_ADS_AVAILABLE = False

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


# ── Campaign creation ─────────────────────────────────────────────────────────

# Polish cities → Google Ads location criteria IDs
POLISH_CITIES_GOOGLE_IDS = {
    "warszawa":      1011634,
    "warsaw":        1011634,
    "kraków":        1010782,
    "krakow":        1010782,
    "wrocław":       1011636,
    "wroclaw":       1011636,
    "poznań":        1011597,
    "poznan":        1011597,
    "gdańsk":        1011528,
    "gdansk":        1011528,
    "łódź":          1011554,
    "lodz":          1011554,
    "katowice":      1010773,
    "szczecin":      1011623,
    "bydgoszcz":     1011505,
    "lublin":        1011556,
    "białystok":     1011494,
    "bialystok":     1011494,
    "gdynia":        1011529,
    "częstochowa":   1011516,
    "czestochowa":   1011516,
    "rzeszów":       1011617,
    "rzeszow":       1011617,
    "toruń":         1011627,
    "torun":         1011627,
    "sosnowiec":     1011621,
    "kielce":        1011541,
    "radom":         1011610,
    "gliwice":       1011533,
    "zabrze":        1011639,
    "olsztyn":       1011584,
    "trójmiasto":    1011528,  # fallback do Gdańska
}

# Poland country ID
_POLAND_GEO_ID = 2616

_CAMPAIGN_TYPE_MAP = {
    "search":          "SEARCH",
    "performance max": "PERFORMANCE_MAX",
    "performance_max": "PERFORMANCE_MAX",
    "pmax":            "PERFORMANCE_MAX",
    "display":         "DISPLAY",
    "video":           "VIDEO",
    "youtube":         "VIDEO",
    "yt":              "VIDEO",
    "demand gen":      "DEMAND_GEN",
    "demand_gen":      "DEMAND_GEN",
    "shopping":        "SHOPPING",
    "app":             "MULTI_CHANNEL",
}

_BIDDING_FRIENDLY = {
    "MAXIMIZE_CLICKS":       "Maks. kliknięcia",
    "MAXIMIZE_CONVERSIONS":  "Maks. konwersje",
    "TARGET_CPA":            "Docelowy CPA",
    "TARGET_ROAS":           "Docelowy ROAS",
    "MANUAL_CPC":            "Ręczny CPC",
}

_CHANNEL_FRIENDLY = {
    "SEARCH":          "Search",
    "PERFORMANCE_MAX": "Performance Max",
    "DISPLAY":         "Display",
    "VIDEO":           "Video / YouTube",
    "DEMAND_GEN":      "Demand Gen",
    "SHOPPING":        "Shopping",
    "MULTI_CHANNEL":   "App",
}


def _parse_budget_micros(budget_str: str) -> int:
    """Parse budget string (e.g. '50', '50 PLN', '50 zł') → micros (int)."""
    if not budget_str:
        return 10_000_000  # default 10 PLN
    clean = re.sub(r'[^\d.,]', '', str(budget_str)).replace(',', '.')
    try:
        amount = float(clean)
    except ValueError:
        amount = 10.0
    amount = max(1.0, min(amount, 2000.0))
    return int(amount * 1_000_000)


def _map_campaign_type(type_str: str) -> str:
    """Map campaign type string to Google Ads enum name."""
    return _CAMPAIGN_TYPE_MAP.get(str(type_str).lower().strip(), "SEARCH")


def _set_bidding_strategy(campaign, params: dict):
    """Set inline bidding strategy on campaign proto object."""
    strategy = str(params.get("bidding_strategy", "MAXIMIZE_CLICKS")).upper()
    target_cpa = params.get("target_cpa", "")
    target_roas = params.get("target_roas", "")

    if strategy == "TARGET_CPA" and target_cpa:
        try:
            cpa_micros = int(float(re.sub(r'[^\d.]', '', str(target_cpa))) * 1_000_000)
            campaign.target_cpa.target_cpa_micros = cpa_micros
            return "TARGET_CPA"
        except Exception:
            pass
    if strategy == "TARGET_ROAS" and target_roas:
        try:
            roas_raw = float(re.sub(r'[^\d.]', '', str(target_roas)))
            # API expects fraction (e.g. 3.0 for 300% ROAS), user may give 300 or 3.0
            roas_fraction = roas_raw / 100 if roas_raw > 10 else roas_raw
            campaign.target_roas.target_roas = roas_fraction
            return "TARGET_ROAS"
        except Exception:
            pass
    if strategy == "MAXIMIZE_CONVERSIONS":
        campaign.maximize_conversions.target_spend_micros = 0
        return "MAXIMIZE_CONVERSIONS"
    if strategy == "MANUAL_CPC":
        campaign.manual_cpc.enhanced_cpc_enabled = False
        return "MANUAL_CPC"
    # Default: MAXIMIZE_CLICKS
    campaign.maximize_clicks.target_spend_micros = 0
    return "MAXIMIZE_CLICKS"


def _detect_google_client(params: dict, accounts_map: dict) -> str | None:
    """Detect client name from wizard JSON params. Returns key matching accounts_map or None."""
    candidates = [
        params.get("client_name", ""),
        params.get("brand_name", ""),
        params.get("campaign_name", ""),
    ]
    for url_field in ("website_url", "landing_page_url"):
        url = params.get(url_field, "")
        if url and "patoagencja.com" not in url:
            # Try to extract domain root
            m = re.search(r'https?://(?:www\.)?([^/]+)', url)
            if m:
                candidates.append(m.group(1))

    for cand in candidates:
        if not cand:
            continue
        cand_lower = str(cand).lower()
        for key in accounts_map:
            key_lower = key.lower()
            if key_lower in cand_lower or cand_lower in key_lower:
                return key
    return None


def create_google_campaign_draft(params: dict, customer_id: str) -> dict:
    """
    Create a PAUSED campaign in Google Ads from wizard JSON params.
    Returns dict with resource names, or {"error": str} on failure.
    """
    if not google_ads_client:
        return {"error": "Google Ads API nie jest skonfigurowane."}

    customer_id = customer_id.replace("-", "").replace(" ", "")
    campaign_name = params.get("campaign_name") or "Kampania Google Ads"
    channel_type_key = _map_campaign_type(params.get("campaign_type", "Search"))
    daily_budget_micros = _parse_budget_micros(params.get("daily_budget", "10"))

    start_date_raw = params.get("start_date") or datetime.now().strftime("%Y-%m-%d")
    start_date = re.sub(r'\D', '', str(start_date_raw))[:8] or datetime.now().strftime("%Y%m%d")

    end_date_raw = params.get("end_date", "")
    end_date = re.sub(r'\D', '', str(end_date_raw))[:8] if end_date_raw else ""

    try:
        # ── 1. Campaign Budget ──────────────────────────────────────────────
        budget_service = google_ads_client.get_service("CampaignBudgetService")
        budget_op = google_ads_client.get_type("CampaignBudgetOperation")
        budget = budget_op.create
        budget.name = f"{campaign_name} - Budżet"
        budget.delivery_method = google_ads_client.enums.BudgetDeliveryMethodEnum.STANDARD
        budget.amount_micros = daily_budget_micros

        budget_resp = budget_service.mutate_campaign_budgets(
            customer_id=customer_id, operations=[budget_op]
        )
        budget_resource = budget_resp.results[0].resource_name

        # ── 2. Campaign ────────────────────────────────────────────────────
        campaign_service = google_ads_client.get_service("CampaignService")
        campaign_op = google_ads_client.get_type("CampaignOperation")
        campaign = campaign_op.create
        campaign.name = campaign_name
        campaign.status = google_ads_client.enums.CampaignStatusEnum.PAUSED
        campaign.campaign_budget = budget_resource
        campaign.start_date = start_date
        if end_date:
            campaign.end_date = end_date

        channel_enum = getattr(
            google_ads_client.enums.AdvertisingChannelTypeEnum, channel_type_key, None
        )
        if channel_enum is not None:
            campaign.advertising_channel_type = channel_enum

        applied_strategy = _set_bidding_strategy(campaign, params)

        campaign_resp = campaign_service.mutate_campaigns(
            customer_id=customer_id, operations=[campaign_op]
        )
        campaign_resource = campaign_resp.results[0].resource_name
        campaign_id = campaign_resource.split("/")[-1]

        # ── 3. Location targeting ──────────────────────────────────────────
        locations_raw = params.get("locations", []) or []
        country_raw = str(params.get("country", "")).lower()
        geo_ids = []

        for loc in locations_raw:
            loc_lower = str(loc).lower()
            if loc_lower in POLISH_CITIES_GOOGLE_IDS:
                geo_ids.append(POLISH_CITIES_GOOGLE_IDS[loc_lower])

        if not geo_ids and ("polska" in country_raw or "poland" in country_raw or "pl" in country_raw):
            geo_ids.append(_POLAND_GEO_ID)

        if not geo_ids:
            geo_ids.append(_POLAND_GEO_ID)  # safe default

        criterion_service = google_ads_client.get_service("CampaignCriterionService")
        crit_ops = []
        for geo_id in geo_ids:
            geo_op = google_ads_client.get_type("CampaignCriterionOperation")
            crit = geo_op.create
            crit.campaign = campaign_resource
            crit.location.geo_target_constant = (
                google_ads_client.get_service("GeoTargetConstantService")
                .geo_target_constant_path(geo_id)
            )
            crit_ops.append(geo_op)

        if crit_ops:
            criterion_service.mutate_campaign_criteria(
                customer_id=customer_id, operations=crit_ops
            )

        # ── 4. Ad Group (Search / Display / Video / Demand Gen) ────────────
        adgroup_resource = None
        ad_resource = None
        keyword_count = 0

        if channel_type_key not in ("PERFORMANCE_MAX", "SHOPPING"):
            adgroup_service = google_ads_client.get_service("AdGroupService")
            adgroup_op = google_ads_client.get_type("AdGroupOperation")
            adgroup = adgroup_op.create
            adgroup.name = f"{campaign_name} - Grupa 1"
            adgroup.campaign = campaign_resource
            adgroup.status = google_ads_client.enums.AdGroupStatusEnum.ENABLED

            if channel_type_key == "SEARCH":
                adgroup.type_ = google_ads_client.enums.AdGroupTypeEnum.SEARCH_STANDARD
            elif channel_type_key == "DISPLAY":
                adgroup.type_ = google_ads_client.enums.AdGroupTypeEnum.DISPLAY_STANDARD

            adgroup_resp = adgroup_service.mutate_ad_groups(
                customer_id=customer_id, operations=[adgroup_op]
            )
            adgroup_resource = adgroup_resp.results[0].resource_name

            # ── 5. Keywords (Search only) ──────────────────────────────────
            if channel_type_key == "SEARCH":
                keywords = params.get("keywords", []) or []
                if keywords:
                    kw_service = google_ads_client.get_service("AdGroupCriterionService")
                    kw_ops = []
                    for kw in keywords[:20]:
                        kw_op = google_ads_client.get_type("AdGroupCriterionOperation")
                        kw_crit = kw_op.create
                        kw_crit.ad_group = adgroup_resource
                        kw_crit.status = google_ads_client.enums.AdGroupCriterionStatusEnum.ENABLED
                        kw_crit.keyword.text = str(kw)[:80]
                        kw_crit.keyword.match_type = (
                            google_ads_client.enums.KeywordMatchTypeEnum.PHRASE
                        )
                        kw_ops.append(kw_op)
                    if kw_ops:
                        kw_service.mutate_ad_group_criteria(
                            customer_id=customer_id, operations=kw_ops
                        )
                        keyword_count = len(kw_ops)

                # ── 6. RSA Ad (Search) ─────────────────────────────────────
                ads_data = params.get("ads") or {}
                headlines = ads_data.get("headlines", []) or []
                descriptions = ads_data.get("descriptions", []) or []
                landing_url = params.get("landing_page_url") or params.get("website_url", "")

                if len(headlines) >= 3 and len(descriptions) >= 1 and landing_url:
                    ad_service = google_ads_client.get_service("AdGroupAdService")
                    ad_op = google_ads_client.get_type("AdGroupAdOperation")
                    ad_group_ad = ad_op.create
                    ad_group_ad.ad_group = adgroup_resource
                    ad_group_ad.status = google_ads_client.enums.AdGroupAdStatusEnum.PAUSED

                    rsa = ad_group_ad.ad.responsive_search_ad
                    ad_group_ad.ad.final_urls.append(landing_url)

                    for h in headlines[:15]:
                        asset = google_ads_client.get_type("AdTextAsset")
                        asset.text = str(h)[:30]
                        rsa.headlines.append(asset)

                    for d in descriptions[:4]:
                        asset = google_ads_client.get_type("AdTextAsset")
                        asset.text = str(d)[:90]
                        rsa.descriptions.append(asset)

                    paths = ads_data.get("paths", []) or []
                    if paths:
                        rsa.path1 = str(paths[0])[:15]
                    if len(paths) > 1:
                        rsa.path2 = str(paths[1])[:15]

                    ad_resp = ad_service.mutate_ad_group_ads(
                        customer_id=customer_id, operations=[ad_op]
                    )
                    ad_resource = ad_resp.results[0].resource_name

        return {
            "campaign_id":        campaign_id,
            "campaign_resource":  campaign_resource,
            "budget_resource":    budget_resource,
            "adgroup_resource":   adgroup_resource,
            "ad_resource":        ad_resource,
            "keyword_count":      keyword_count,
            "customer_id":        customer_id,
            "applied_strategy":   applied_strategy,
            "channel_type":       channel_type_key,
            "params":             params,
        }

    except Exception as e:
        logger.error("Błąd tworzenia kampanii Google Ads: %s", e, exc_info=True)
        return {"error": str(e)}


def generate_google_campaign_preview(params: dict, draft: dict) -> str:
    """Generate Slack-formatted preview of a Google Ads campaign draft."""
    channel_type = draft.get("channel_type") or _map_campaign_type(
        params.get("campaign_type", "Search")
    )
    channel_friendly = _CHANNEL_FRIENDLY.get(channel_type, channel_type)

    campaign_name = params.get("campaign_name", "—")
    daily_budget = params.get("daily_budget", "?")
    bidding = _BIDDING_FRIENDLY.get(draft.get("applied_strategy", ""), draft.get("applied_strategy", "?"))

    locations_raw = params.get("locations", []) or []
    country = params.get("country", "")
    if locations_raw:
        location_str = ", ".join(str(l) for l in locations_raw)
    elif country:
        location_str = str(country)
    else:
        location_str = "Polska"

    keywords = params.get("keywords", []) or []
    neg_keywords = params.get("negative_keywords", []) or []
    ads_data = params.get("ads") or {}
    headlines = ads_data.get("headlines", []) or []
    descriptions = ads_data.get("descriptions", []) or []
    landing_url = params.get("landing_page_url") or params.get("website_url", "—")

    start_date = params.get("start_date", "dziś")
    end_date = params.get("end_date", "")

    try:
        budget_val = float(re.sub(r'[^\d.]', '', str(daily_budget)))
        weekly_est = f"~{budget_val * 7:.0f} PLN"
    except Exception:
        weekly_est = "?"

    lines = [
        "📋 *Szkic kampanii Google Ads — wyłączona*",
        "",
        f"*Typ:* {channel_friendly}",
        f"*Nazwa:* {campaign_name}",
        f"*Budżet dzienny:* {daily_budget} PLN | Tygodniowo: {weekly_est}",
        f"*Strategia:* {bidding}",
        f"*Lokalizacja:* {location_str}",
        f"*Start:* {start_date}" + (f" | Koniec: {end_date}" if end_date else ""),
        f"*Landing page:* {landing_url}",
    ]

    if keywords:
        kw_preview = ", ".join(f"`{k}`" for k in keywords[:8])
        if len(keywords) > 8:
            kw_preview += f" +{len(keywords) - 8} więcej"
        lines.append(f"*Słowa kluczowe ({len(keywords)}):* {kw_preview}")

    if neg_keywords:
        neg_preview = ", ".join(f"`{k}`" for k in neg_keywords[:5])
        lines.append(f"*Wykluczenia:* {neg_preview}")

    if headlines:
        h_preview = " | ".join(f'"{h}"' for h in headlines[:3])
        lines.append(f"*Nagłówki:* {h_preview}")

    if descriptions:
        d_preview = f'"{descriptions[0]}"'
        lines.append(f"*Opis:* {d_preview}")

    if draft.get("keyword_count", 0) > 0:
        lines.append(f"✅ Dodano {draft['keyword_count']} słów kluczowych")

    if draft.get("ad_resource"):
        lines.append("✅ Reklama RSA dodana")
    elif channel_type == "SEARCH" and len(headlines) < 3:
        lines.append("⚠️ Brak nagłówków — reklama RSA nie została dodana (za mało danych)")

    lines += [
        "",
        f"🆔 Campaign ID: `{draft.get('campaign_id', '?')}`",
        "",
        "⏸️ *Kampania jest WSTRZYMANA* — włącz ją ręcznie w Google Ads gdy będziesz gotowy.",
        "🔗 Panel: https://ads.google.com",
    ]

    return "\n".join(lines)
