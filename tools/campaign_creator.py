"""
Meta Ads Campaign Creator — tworzenie kampanii z approval workflow.

Etapy: file handling → parsing → targeting → draft (PAUSED) → preview → approval/cancel.
"""
import os
import json
import time
import logging
import tempfile
import requests
from datetime import datetime, timedelta

import _ctx
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adimage import AdImage
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.targetingsearch import TargetingSearch
from facebook_business.exceptions import FacebookRequestError

from config.constants import (
    MAX_DAILY_BUDGET, MAX_TOTAL_BUDGET,
    META_ACCOUNT_IDS, META_PAGE_IDS,
    POLISH_CITIES_FB_IDS, OBJECTIVE_FRIENDLY,
)

logger = logging.getLogger(__name__)

try:
    FacebookAdsApi.init(access_token=os.environ.get("META_ACCESS_TOKEN"))
except Exception as _e:
    logger.warning(f"Campaign creator: Meta API init warning: {_e}")

# Interest search cache (keyword → list of {id, name})
_interests_cache: dict = {}

# Allowed MIME types and extensions
_ALLOWED_TYPES = {
    "image/jpeg":     "jpg",
    "image/jpg":      "jpg",
    "image/png":      "png",
    "image/gif":      "gif",
    "image/webp":     "webp",
    "video/mp4":      "mp4",
    "video/quicktime": "mov",
}
_MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 MB


# ── ETAP 1: FILE HANDLING ──────────────────────────────────────────────────────

def download_slack_files(file_ids: list) -> list:
    """
    Pobiera pliki uploadowane na Slack.
    Returns: lista (file_name, file_data_bytes, mime_type)
    """
    results = []
    token = os.environ.get("SLACK_BOT_TOKEN", "")

    for file_id in file_ids:
        try:
            info = _ctx.app.client.files_info(file=file_id)
            file_obj = info["file"]
            mime = file_obj.get("mimetype", "")

            if mime not in _ALLOWED_TYPES:
                logger.warning(f"Unsupported file type: {mime} ({file_obj.get('name')})")
                continue

            size = file_obj.get("size", 0)
            if size > _MAX_FILE_SIZE:
                logger.warning(f"File too large: {file_obj.get('name')} ({size / 1024 / 1024:.1f} MB)")
                continue

            url = file_obj.get("url_private_download") or file_obj.get("url_private")
            if not url:
                logger.warning(f"No download URL for file {file_id}")
                continue

            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
            resp.raise_for_status()

            results.append((file_obj.get("name", f"file.{_ALLOWED_TYPES[mime]}"), resp.content, mime))
            logger.info(f"Downloaded: {file_obj.get('name')} ({len(resp.content) / 1024:.0f} KB)")

        except Exception as e:
            logger.error(f"download_slack_files error for {file_id}: {e}")

    return results


def upload_creative_to_meta(account_id: str, file_data: bytes, file_type: str, file_name: str) -> dict:
    """
    Uploaduje kreację do Meta Ads.
    Returns: {'type': 'image'|'video', 'hash': str|None, 'id': str}
    Retry 3x on failure.
    """
    is_video = file_type in ("video/mp4", "video/quicktime")
    ext = _ALLOWED_TYPES.get(file_type, "jpg")
    last_exc = None

    for attempt in range(3):
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name

            try:
                if is_video:
                    video = AdVideo(parent_id=account_id)
                    video[AdVideo.Field.filepath] = tmp_path
                    video[AdVideo.Field.name] = file_name
                    video.remote_create()
                    return {"type": "video", "id": video["id"], "hash": None}
                else:
                    image = AdImage(parent_id=account_id)
                    image[AdImage.Field.filename] = tmp_path
                    image.remote_create()
                    return {"type": "image", "hash": image["hash"], "id": image.get("id", "")}
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        except FacebookRequestError as e:
            last_exc = e
            logger.error(f"upload_creative attempt {attempt+1} Meta error: {e.api_error_message()}")
            if attempt < 2:
                time.sleep(2 ** attempt)
        except Exception as e:
            last_exc = e
            logger.error(f"upload_creative attempt {attempt+1} error: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"Upload failed after 3 attempts: {last_exc}")


# ── ETAP 2: CAMPAIGN PARSING ──────────────────────────────────────────────────

def parse_campaign_request(user_message: str, files: list) -> dict:
    """
    Używa Claude do wyciągnięcia parametrów kampanii z wiadomości.
    Returns: dict z parametrami kampanii.
    """
    today      = datetime.now().strftime("%Y-%m-%d")
    tomorrow   = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    file_info  = f"\nZałączone pliki: {', '.join(f[0] for f in files)}" if files else ""

    prompt = f"""Jesteś asystentem który parsuje prośby tworzenia kampanii Meta Ads.
Wyciągnij parametry z wiadomości i zwróć czysty JSON (bez markdown, bez komentarzy).

Dzisiaj: {today}
Wiadomość: {user_message}{file_info}

Zwróć dokładnie ten JSON (null gdy brak danych):
{{
  "client_name": "dre|instax|m2|pato",
  "campaign_name": "nazwa kampanii",
  "objective": "OUTCOME_TRAFFIC|OUTCOME_ENGAGEMENT|OUTCOME_LEADS|OUTCOME_SALES|OUTCOME_AWARENESS|CONVERSIONS|REACH|BRAND_AWARENESS|LEAD_GENERATION|VIDEO_VIEWS",
  "daily_budget": liczba_PLN,
  "website_url": "https://...",
  "ad_copy": "tekst reklamy",
  "targeting": {{
    "gender": "male|female|all",
    "age_min": liczba,
    "age_max": liczba,
    "locations": ["Warszawa", "Kraków"],
    "interests": ["interior design", "home decor"]
  }},
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD lub null",
  "call_to_action": "LEARN_MORE|SHOP_NOW|SIGN_UP|GET_QUOTE|CONTACT_US|BOOK_TRAVEL"
}}

Zasady mapowania:
- "dre"/"drzwi" → client_name="dre"
- "instax"/"fuji"/"fujifilm" → client_name="instax"
- "m2"/"nieruchomości" → client_name="m2"
- cel "traffic"/"ruch" → OUTCOME_TRAFFIC | "konwersje"/"sprzedaż" → OUTCOME_SALES | "zasięg" → REACH | "zaangażowanie" → OUTCOME_ENGAGEMENT | "leady" → OUTCOME_LEADS
- start_date domyślnie jutro ({tomorrow})
- Odpowiedz TYLKO JSON."""

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Strip markdown code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        params = json.loads(text)

        # Defaults — użyj or aby zastąpić też None (nie tylko brak klucza)
        client = (params.get("client_name") or "kampania").upper()
        params["objective"]      = params.get("objective")      or "OUTCOME_TRAFFIC"
        params["campaign_name"]  = params.get("campaign_name")  or f"{client} – {today}"
        params["daily_budget"]   = params.get("daily_budget")   or 100
        params["call_to_action"] = params.get("call_to_action") or "LEARN_MORE"
        params["website_url"]    = params.get("website_url")    or "https://patoagencja.com"
        params["start_date"]     = params.get("start_date")     or tomorrow

        if not params.get("targeting"):
            params["targeting"] = {
                "gender": "all", "age_min": 18, "age_max": 65,
                "locations": ["Polska"], "interests": [],
            }

        logger.info(f"parse_campaign_request: {json.dumps(params, ensure_ascii=False)}")
        return params

    except Exception as e:
        logger.error(f"parse_campaign_request error: {e}")
        return {
            "client_name":    None,
            "campaign_name":  "Nowa kampania",
            "objective":      "OUTCOME_TRAFFIC",
            "daily_budget":   100,
            "website_url":    "https://patoagencja.com",
            "ad_copy":        "",
            "targeting":      {"gender": "all", "age_min": 18, "age_max": 65, "locations": [], "interests": []},
            "start_date":     tomorrow,
            "end_date":       None,
            "call_to_action": "LEARN_MORE",
        }


# ── ETAP 3: TARGETING BUILDER ────────────────────────────────────────────────

def search_meta_interests(keyword: str) -> list:
    """
    Szuka interests w Meta Ads API po słowie kluczowym. Cache wyników.
    Returns: lista {id, name}
    """
    key = keyword.lower().strip()
    if key in _interests_cache:
        return _interests_cache[key]

    try:
        result = TargetingSearch.search(params={
            "q":     keyword,
            "type":  "adinterest",
            "limit": 5,
        })
        items = [{"id": str(r["id"]), "name": r["name"]} for r in result]
        _interests_cache[key] = items
        logger.info(f"Interest search '{keyword}': {len(items)} results")
        return items
    except Exception as e:
        logger.error(f"search_meta_interests error for '{keyword}': {e}")
        return []


def build_meta_targeting(targeting_dict: dict) -> dict:
    """
    Konwertuje user-friendly targeting → Meta Ads API format.
    """
    result = {}

    # Gender
    gender = (targeting_dict.get("gender") or "all").lower()
    if gender == "male":
        result["genders"] = [1]
    elif gender == "female":
        result["genders"] = [2]
    # "all" → brak klucza genders (Meta domyślnie oba)

    # Age
    result["age_min"] = int(targeting_dict.get("age_min") or 18)
    result["age_max"] = int(targeting_dict.get("age_max") or 65)

    # Locations
    locations = targeting_dict.get("locations") or []
    cities    = []
    countries = []

    for loc in locations:
        loc_lower = loc.lower().strip()
        if loc_lower in ("polska", "poland", "pl"):
            countries.append("PL")
        elif loc_lower in POLISH_CITIES_FB_IDS:
            cities.append(POLISH_CITIES_FB_IDS[loc_lower])
        else:
            # Partial match
            matched = next(
                (v for k, v in POLISH_CITIES_FB_IDS.items()
                 if loc_lower in k or k in loc_lower),
                None,
            )
            if matched:
                cities.append(matched)
            elif len(loc) == 2:
                countries.append(loc.upper())

    geo = {}
    if cities:
        geo["cities"] = cities
    if countries:
        geo["countries"] = countries
    if not geo:
        geo["countries"] = ["PL"]
    result["geo_locations"] = geo

    # Interests
    interests_kws = targeting_dict.get("interests") or []
    interest_objs = []
    for kw in interests_kws:
        found = search_meta_interests(kw)
        if found:
            interest_objs.append(found[0])

    if interest_objs:
        result["flexible_spec"] = [{"interests": interest_objs}]

    return result


def get_meta_account_id(client_name: str) -> str:
    """Zwraca Meta Ads account ID dla klienta."""
    return META_ACCOUNT_IDS.get((client_name or "").lower().strip(), "")


# ── ETAP 8: WALIDACJA ─────────────────────────────────────────────────────────

def validate_campaign_params(params: dict) -> list:
    """Waliduje parametry kampanii. Return: lista błędów (pusty = OK)."""
    errors = []

    client = params.get("client_name")
    if not client:
        errors.append("Nie podano klienta (dre / instax / m2 / pato)")
    elif not get_meta_account_id(client):
        errors.append(
            f"Nieznany klient: '{client}' albo brak konfiguracji `{client.upper()}_META_ACCOUNT_ID`"
        )

    budget = float(params.get("daily_budget") or 0)
    if budget <= 0:
        errors.append("Budżet dzienny musi być > 0 PLN")
    elif budget > MAX_DAILY_BUDGET:
        errors.append(f"Budżet {budget:.0f} PLN/dzień przekracza limit bezpieczeństwa {MAX_DAILY_BUDGET} PLN")

    if budget * 30 > MAX_TOTAL_BUDGET:
        errors.append(
            f"Szacowany budżet miesięczny {budget * 30:.0f} PLN przekracza limit {MAX_TOTAL_BUDGET} PLN"
        )

    if not params.get("ad_copy"):
        errors.append("Brak copy (tekst reklamy)")

    if not params.get("targeting"):
        errors.append("Brak targetingu")

    return errors


# ── ETAP 4: CAMPAIGN BUILDER (DRAFT) ─────────────────────────────────────────

def create_campaign_draft(
    account_id:      str,
    campaign_params: dict,
    creatives:       list,
    targeting:       dict,
) -> dict:
    """
    Tworzy szkic kampanii w Meta Ads — wszystko startuje ze statusem PAUSED.
    creatives: lista {'type': 'image'|'video', 'hash': str, 'id': str}
    Returns: {'campaign_id', 'adset_id', 'ad_ids', 'params', 'account_id'}
    """
    objective = campaign_params.get("objective", "OUTCOME_TRAFFIC")
    page_id   = META_PAGE_IDS.get((campaign_params.get("client_name") or "").lower(), "")

    # Normalize legacy objective names → Meta API v19 names
    _legacy_obj_map = {
        "TRAFFIC":     "OUTCOME_TRAFFIC",
        "ENGAGEMENT":  "OUTCOME_ENGAGEMENT",
        "APP_INSTALLS":"OUTCOME_APP_PROMOTION",
    }
    objective = _legacy_obj_map.get(objective, objective)

    opt_goal_map = {
        "OUTCOME_TRAFFIC":       "LINK_CLICKS",
        "OUTCOME_ENGAGEMENT":    "POST_ENGAGEMENT",
        "OUTCOME_LEADS":         "LEAD_GENERATION",
        "OUTCOME_SALES":         "OFFSITE_CONVERSIONS",
        "OUTCOME_AWARENESS":     "REACH",
        "OUTCOME_APP_PROMOTION": "APP_INSTALLS",
        "CONVERSIONS":           "OFFSITE_CONVERSIONS",
        "REACH":                 "REACH",
        "BRAND_AWARENESS":       "BRAND_AWARENESS",
        "LEAD_GENERATION":       "LEAD_GENERATION",
        "VIDEO_VIEWS":           "THRUPLAY",
        "POST_ENGAGEMENT":       "POST_ENGAGEMENT",
        "LINK_CLICKS":           "LINK_CLICKS",
    }
    optimization_goal = opt_goal_map.get(objective, "LINK_CLICKS")

    _token   = os.environ.get("META_ACCESS_TOKEN", "")
    _api_url = f"https://graph.facebook.com/v19.0/{account_id}"

    def _graph_post(endpoint: str, data: dict) -> dict:
        resp = requests.post(
            f"{_api_url}/{endpoint}",
            params={"access_token": _token},
            json=data,
            timeout=30,
        )
        body = resp.json()
        if "error" in body:
            err = body["error"]
            msg = (
                f"Meta API /{endpoint} [{err.get('code')}/{err.get('error_subcode','')}] "
                f"{err.get('message')} | {err.get('error_user_msg', '')}"
            )
            raise Exception(msg)
        return body

    # ── A: Create Campaign ────────────────────────────────────────────────────
    camp_body = _graph_post("campaigns", {
        "name":                          campaign_params["campaign_name"],
        "objective":                     objective,
        "status":                        "PAUSED",
        "special_ad_categories":         [],
        "is_adset_budget_sharing_enabled": False,
    })
    campaign_id = camp_body["id"]
    logger.info(f"Campaign draft created: {campaign_id} ({campaign_params['campaign_name']})")

    # ── B: Create AdSet ───────────────────────────────────────────────────────
    targeting_with_auto = {
        **targeting,
        "targeting_automation": {"advantage_audience": 0},
    }
    adset_body_data = {
        "name":              f"{campaign_params['campaign_name']} - AdSet 1",
        "campaign_id":       campaign_id,
        "daily_budget":      int(float(campaign_params["daily_budget"]) * 100),
        "billing_event":     "IMPRESSIONS",
        "optimization_goal": optimization_goal,
        "bid_strategy":      "LOWEST_COST_WITHOUT_CAP",
        "targeting":         targeting_with_auto,
        "status":            "PAUSED",
        "start_time":        campaign_params["start_date"],
    }
    if campaign_params.get("end_date"):
        adset_body_data["end_time"] = campaign_params["end_date"]

    adset_body = _graph_post("adsets", adset_body_data)
    adset_id   = adset_body["id"]
    logger.info(f"AdSet created: {adset_id}")

    # ── C: Create Ads (jedna reklama na kreację) ───────────────────────────────
    ad_ids    = []
    website   = campaign_params.get("website_url", "https://patoagencja.com")
    ad_copy   = campaign_params.get("ad_copy", "")
    cta       = campaign_params.get("call_to_action", "LEARN_MORE")

    def _get_existing_page_post(pid: str):
        try:
            resp = requests.get(
                f"https://graph.facebook.com/v19.0/{pid}/posts",
                params={"access_token": _token, "fields": "id", "limit": 5},
                timeout=15,
            )
            data = resp.json().get("data", [])
            if data:
                return data[0]["id"]
        except Exception as e:
            logger.warning(f"_get_existing_page_post error: {e}")
        return None

    existing_post_id = _get_existing_page_post(page_id) if page_id else None
    if existing_post_id:
        logger.info(f"Using existing page post: {existing_post_id}")

    for i, creative in enumerate(creatives):
        try:
            if existing_post_id:
                creative_payload = {
                    "name":            f"{campaign_params['campaign_name']} - Creative {i+1}",
                    "object_story_id": existing_post_id,
                }
            else:
                if creative["type"] == "image":
                    story_spec = {
                        "link_data": {
                            "image_hash":     creative["hash"],
                            "link":           website,
                            "message":        ad_copy,
                            "call_to_action": {"type": cta, "value": {"link": website}},
                        }
                    }
                else:  # video
                    story_spec = {
                        "video_data": {
                            "video_id":       creative["id"],
                            "title":          campaign_params["campaign_name"],
                            "message":        ad_copy,
                            "call_to_action": {"type": cta, "value": {"link": website}},
                        }
                    }
                if page_id:
                    story_spec["page_id"] = page_id
                creative_payload = {
                    "name":              f"{campaign_params['campaign_name']} - Creative {i+1}",
                    "object_story_spec": story_spec,
                }

            logger.info(f"AdCreative payload: {json.dumps(creative_payload, ensure_ascii=False)}")
            creative_body = _graph_post("adcreatives", creative_payload)
            creative_id = creative_body["id"]

            ad_body = _graph_post("ads", {
                "name":     f"{campaign_params['campaign_name']} - Ad {i+1}",
                "adset_id": adset_id,
                "status":   "PAUSED",
                "creative": {"creative_id": creative_id},
            })
            ad_ids.append(ad_body["id"])
            logger.info(f"Ad {i+1} created: {ad_body['id']}")

        except Exception as e:
            logger.error(f"Ad {i+1} creation error: {e}")

    result = {
        "campaign_id": campaign_id,
        "adset_id":    adset_id,
        "ad_ids":      ad_ids,
        "params":      campaign_params,
        "account_id":  account_id,
    }

    # Zapisz w _ctx do późniejszego approval
    _ctx.campaign_drafts[campaign_id] = result
    logger.info(f"Draft saved to _ctx.campaign_drafts['{campaign_id}']")
    return result


# ── ETAP 5: PREVIEW GENERATOR ────────────────────────────────────────────────

def generate_campaign_preview(
    campaign_params: dict,
    targeting_input: dict,
    creative_count:  int,
    draft_ids:       dict,
) -> str:
    """Generuje czytelny Slack preview kampanii do zatwierdzenia."""
    t = targeting_input or {}

    gender_map  = {"male": "Mężczyźni", "female": "Kobiety", "all": "Wszyscy"}
    gender_str  = gender_map.get((t.get("gender") or "all").lower(), "Wszyscy")
    locs_str    = ", ".join(t.get("locations") or ["Polska"])
    interests   = t.get("interests") or []
    int_str     = ", ".join(interests) if interests else "—"

    obj     = campaign_params.get("objective", "TRAFFIC")
    obj_str = OBJECTIVE_FRIENDLY.get(obj, obj)

    budget     = float(campaign_params.get("daily_budget") or 0)
    total_7d   = budget * 7
    start      = campaign_params.get("start_date", "—")
    end        = campaign_params.get("end_date") or "Bez końca"
    client     = (campaign_params.get("client_name") or "").upper()
    camp_name  = campaign_params.get("campaign_name", "—")
    ad_copy    = campaign_params.get("ad_copy") or "—"
    url        = campaign_params.get("website_url") or "—"
    cta        = campaign_params.get("call_to_action", "LEARN_MORE")
    camp_id    = draft_ids["campaign_id"]

    return (
        f"📊 *PREVIEW KAMPANII — {client}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *SETUP:*\n"
        f"Kampania: _{camp_name}_\n"
        f"Cel: {obj_str}\n"
        f"Budżet: *{budget:.0f} PLN/dzień*\n\n"
        f"👥 *TARGETING:*\n"
        f"• Płeć: {gender_str}\n"
        f"• Wiek: {t.get('age_min', 18)}–{t.get('age_max', 65)} lat\n"
        f"• Lokalizacja: {locs_str}\n"
        f"• Zainteresowania: {int_str}\n\n"
        f"🎨 *KREACJE:* {creative_count} szt.\n\n"
        f"📝 *COPY:*\n_{ad_copy}_\n\n"
        f"🔗 *Link:* {url}\n"
        f"📣 *CTA:* {cta}\n\n"
        f"📅 *HARMONOGRAM:*\n"
        f"• Start: {start}\n"
        f"• Koniec: {end}\n\n"
        f"💰 *SZACUNKI:*\n"
        f"• Budget 7 dni: ~{total_7d:.0f} PLN\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *Kampania jest w trybie DRAFT (zatrzymana)*\n\n"
        f"*Aby uruchomić:*\n"
        f"`@Sebol zatwierdź kampanię {camp_id}`\n\n"
        f"*Aby anulować:*\n"
        f"`@Sebol anuluj kampanię {camp_id}`\n"
        f"_Campaign ID: {camp_id}_"
    )


# ── ETAP 6: APPROVAL & EXECUTION ──────────────────────────────────────────────

def approve_and_launch_campaign(campaign_id: str) -> str:
    """
    Aktywuje kampanię + adset + wszystkie reklamy.
    Returns: czytelny komunikat sukcesu lub błędu.
    """
    draft = _ctx.campaign_drafts.get(campaign_id)
    if not draft:
        return (
            f"❌ Nie znalazłem draftu kampanii `{campaign_id}` w pamięci.\n"
            f"Możliwy reset bota — sprawdź Ads Manager i aktywuj ręcznie."
        )

    try:
        # Aktywuj Campaign
        campaign = Campaign(campaign_id)
        campaign.update({Campaign.Field.status: "ACTIVE"})
        campaign.remote_update()

        # Aktywuj AdSet
        adset = AdSet(draft["adset_id"])
        adset.update({AdSet.Field.status: "ACTIVE"})
        adset.remote_update()

        # Aktywuj wszystkie Ads
        for ad_id in draft.get("ad_ids", []):
            ad = Ad(ad_id)
            ad.update({Ad.Field.status: "ACTIVE"})
            ad.remote_update()

        # Usuń draft z pamięci
        del _ctx.campaign_drafts[campaign_id]

        account_raw = draft.get("account_id", "").replace("act_", "")
        client_name = (draft["params"].get("client_name") or "").upper()
        camp_name   = draft["params"].get("campaign_name", "")
        ad_count    = len(draft.get("ad_ids", []))
        ads_link    = f"https://business.facebook.com/adsmanager/manage/campaigns?act={account_raw}"

        logger.info(f"Campaign launched: {campaign_id} ({camp_name})")
        return (
            f"🚀 *Kampania uruchomiona!*\n\n"
            f"Klient: *{client_name}*\n"
            f"Kampania: _{camp_name}_\n"
            f"Reklamy: {ad_count} szt. aktywne\n"
            f"ID: `{campaign_id}`\n\n"
            f"📊 <{ads_link}|Otwórz Ads Manager>"
        )

    except FacebookRequestError as e:
        logger.error(f"approve_and_launch_campaign Meta error: {e}")
        return f"❌ Błąd Meta API: {e.api_error_message()}"
    except Exception as e:
        logger.error(f"approve_and_launch_campaign error: {e}")
        return f"❌ Błąd: {str(e)}"


def cancel_campaign_draft(campaign_id: str) -> str:
    """
    Usuwa draft kampanii (cascade: adset + reklamy).
    Returns: komunikat sukcesu lub błędu.
    """
    try:
        campaign = Campaign(campaign_id)
        campaign.remote_delete()

        if campaign_id in _ctx.campaign_drafts:
            del _ctx.campaign_drafts[campaign_id]

        logger.info(f"Campaign draft deleted: {campaign_id}")
        return f"🗑️ *Kampania anulowana i usunięta.*\nID: `{campaign_id}`"

    except FacebookRequestError as e:
        logger.error(f"cancel_campaign_draft Meta error: {e}")
        return f"❌ Błąd usuwania (Meta API): {e.api_error_message()}"
    except Exception as e:
        logger.error(f"cancel_campaign_draft error: {e}")
        return f"❌ Błąd: {str(e)}"
