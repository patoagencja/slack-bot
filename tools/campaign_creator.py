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
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB (video creatives can be large)


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
  "objective": "OUTCOME_TRAFFIC|OUTCOME_ENGAGEMENT|OUTCOME_LEADS|OUTCOME_SALES|OUTCOME_AWARENESS|OUTCOME_APP_PROMOTION",
  "daily_budget": liczba_PLN,
  "website_url": "https://... lub null jeśli nie podano",
  "ad_copy": "tekst reklamy lub null",
  "targeting": {{
    "gender": "male|female|all",
    "age_min": liczba,
    "age_max": liczba,
    "locations": ["Warszawa", "Kraków"],
    "interests": ["interior design", "home decor"]
  }},
  "publisher_platforms": ["facebook","instagram"] lub null (null = automatyczne),
  "placement_positions": ["feed","story","reels","explore"] lub null (null = automatyczne),
  "cta_enabled": true lub false,
  "call_to_action": "LEARN_MORE|SHOP_NOW|SIGN_UP|GET_QUOTE|CONTACT_US|BOOK_TRAVEL|MESSAGE_PAGE|null",
  "link_enabled": true lub false,
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD lub null"
}}

Zasady mapowania:
- "dre"/"drzwi" → client_name="dre"
- "instax"/"fuji"/"fujifilm" → client_name="instax"
- "m2"/"nieruchomości" → client_name="m2"
- "tc2023"/"timecatchers"/"time catchers" → client_name="tc2023"
- cel "traffic"/"ruch" → OUTCOME_TRAFFIC | "konwersje"/"sprzedaż" → OUTCOME_SALES | "zasięg"/"reach"/"świadomość" → OUTCOME_AWARENESS | "zaangażowanie" → OUTCOME_ENGAGEMENT | "leady" → OUTCOME_LEADS | "app"/"aplikacja" → OUTCOME_APP_PROMOTION
- "tylko Instagram"/"instagram" → publisher_platforms=["instagram"] | "tylko Facebook"/"facebook" → ["facebook"] | brak = null
- "Stories"/"story" → placement_positions zawiera "story" | "Reels"/"reels" → "reels" | "Feed" → "feed"
- "bez buttona"/"no cta"/"bez CTA" → cta_enabled=false, inaczej true
- "bez linku"/"no link" → link_enabled=false, inaczej true
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

        # Hard-normalize objective — Meta API v19 akceptuje tylko OUTCOME_*
        _OBJ_NORMALIZE = {
            "REACH":            "OUTCOME_AWARENESS",
            "BRAND_AWARENESS":  "OUTCOME_AWARENESS",
            "AWARENESS":        "OUTCOME_AWARENESS",
            "CONVERSIONS":      "OUTCOME_SALES",
            "LEAD_GENERATION":  "OUTCOME_LEADS",
            "LEADS":            "OUTCOME_LEADS",
            "VIDEO_VIEWS":      "OUTCOME_ENGAGEMENT",
            "POST_ENGAGEMENT":  "OUTCOME_ENGAGEMENT",
            "ENGAGEMENT":       "OUTCOME_ENGAGEMENT",
            "LINK_CLICKS":      "OUTCOME_TRAFFIC",
            "TRAFFIC":          "OUTCOME_TRAFFIC",
            "APP_INSTALLS":     "OUTCOME_APP_PROMOTION",
        }
        _raw_obj = (params.get("objective") or "").upper().strip()
        params["objective"] = _OBJ_NORMALIZE.get(_raw_obj, _raw_obj) or "OUTCOME_TRAFFIC"

        # Defaults — użyj or aby zastąpić też None (nie tylko brak klucza)
        client = (params.get("client_name") or "kampania").upper()
        params["objective"]           = params.get("objective")      or "OUTCOME_TRAFFIC"
        params["campaign_name"]       = params.get("campaign_name")  or f"{client} – {today}"
        # daily_budget: keep None if Claude didn't detect it (questionnaire will ask)
        if params.get("daily_budget") is not None:
            try:
                params["daily_budget"] = float(params["daily_budget"])
            except (TypeError, ValueError):
                params["daily_budget"] = None
        params["call_to_action"]      = params.get("call_to_action") or "LEARN_MORE"
        params["website_url"]         = params.get("website_url")    or None
        params["start_date"]          = params.get("start_date")     or tomorrow
        # cta_enabled / link_enabled — default True jeśli nie podano
        if params.get("cta_enabled") is None:
            params["cta_enabled"] = True
        if params.get("link_enabled") is None:
            params["link_enabled"] = True
        # publisher_platforms / placement_positions — None = automatyczne
        if "publisher_platforms" not in params:
            params["publisher_platforms"] = None
        if "placement_positions" not in params:
            params["placement_positions"] = None

        if not params.get("targeting"):
            params["targeting"] = {
                "gender": "all", "age_min": 18, "age_max": 65,
                "locations": ["Polska"], "interests": [],
            }

        # ── Fallback: jeśli Claude nie wyciągnął client_name, szukaj po słowach kluczowych ──
        if not params.get("client_name"):
            _msg_l = user_message.lower()
            if any(k in _msg_l for k in ("tc2023", "timecatchers", "time catchers")):
                params["client_name"] = "tc2023"
            elif any(k in _msg_l for k in ("dre", "drzwi", "dzrwi", "dzwri", "drze")):
                params["client_name"] = "dre"
            elif any(k in _msg_l for k in ("instax", "fuji", "fujifilm")):
                params["client_name"] = "instax"
            elif "m2" in _msg_l:
                params["client_name"] = "m2"
            elif "pato" in _msg_l:
                params["client_name"] = "pato"
            if params.get("client_name"):
                logger.info(f"client_name fallback keyword match: {params['client_name']!r}")

        logger.info(f"parse_campaign_request: {json.dumps(params, ensure_ascii=False)}")
        return params

    except Exception as e:
        logger.error(f"parse_campaign_request error: {e}")
        # Keyword fallback nawet gdy Claude rzuci wyjątek (np. brak kredytów API)
        _msg_l = user_message.lower()
        _fallback_client = None
        if any(k in _msg_l for k in ("dre", "drzwi", "dzrwi", "dzwri", "drze")):
            _fallback_client = "dre"
        elif any(k in _msg_l for k in ("instax", "fuji", "fujifilm")):
            _fallback_client = "instax"
        elif "m2" in _msg_l:
            _fallback_client = "m2"
        elif "pato" in _msg_l:
            _fallback_client = "pato"
        if _fallback_client:
            logger.info(f"parse_campaign_request except-fallback client: {_fallback_client!r}")
        # Wyciągnij podstawowe parametry regexem z wiadomości
        import re as _re2
        # Budżet
        _budget_m = _re2.search(r'(\d+)\s*(?:zł|pln|złotych)', _msg_l)
        _budget = float(_budget_m.group(1)) if _budget_m else 100
        # Płeć
        if any(k in _msg_l for k in ("kobiety", "kobieta", "female", "women")):
            _gender = "female"
        elif any(k in _msg_l for k in ("mężczyźni", "mezczyzni", "mężczyzn", "male", "men")):
            _gender = "male"
        else:
            _gender = "all"
        # Wiek (np. "18-32", "25-45")
        _age_m = _re2.search(r'(\d{1,2})\s*[-–]\s*(\d{1,2})', _msg_l)
        _age_min = int(_age_m.group(1)) if _age_m else 18
        _age_max = int(_age_m.group(2)) if _age_m else 65
        # Cel kampanii
        if any(k in _msg_l for k in ("sprzedaż", "sprzedaz", "konwersje", "sales")):
            _obj = "OUTCOME_SALES"
        elif any(k in _msg_l for k in ("leady", "lead")):
            _obj = "OUTCOME_LEADS"
        elif any(k in _msg_l for k in ("zasięg", "zasieg", "awareness", "świadomość")):
            _obj = "OUTCOME_AWARENESS"
        elif any(k in _msg_l for k in ("zaangażowanie", "zaangazowanie", "engagement")):
            _obj = "OUTCOME_ENGAGEMENT"
        else:
            _obj = "OUTCOME_TRAFFIC"
        return {
            "client_name":    _fallback_client,
            "campaign_name":  "Nowa kampania",
            "objective":      _obj,
            "daily_budget":   _budget,
            "website_url":    "https://patoagencja.com",
            "ad_copy":        "",
            "targeting":      {"gender": _gender, "age_min": _age_min, "age_max": _age_max, "locations": ["Polska"], "interests": []},
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

    # ad_copy jest opcjonalne gdy brak plików — bot użyje istniejącego posta ze strony
    # (wymagane dopiero gdy business portfolio zweryfikowane i dark posty będą działać)

    if not params.get("targeting"):
        errors.append("Brak targetingu")

    return errors


# ── ETAP 3b: EXPERT ANALYSIS ──────────────────────────────────────────────────

def generate_campaign_expert_analysis(params: dict, files: list) -> str:
    """
    Analizuje parametry kampanii jak ekspert digital marketingu i zwraca
    sugestie + pytania proaktywne. Wywoływana PRZED stworzeniem kampanii.

    Returns: tekst Slack markdown z analizą ekspercką.
    """
    client     = (params.get("client_name") or "?").upper()
    budget     = float(params.get("daily_budget") or 0)
    objective  = params.get("objective") or "OUTCOME_TRAFFIC"
    tgt        = params.get("targeting") or {}
    platform   = params.get("publisher_platforms") or []
    placements = params.get("placement_positions") or []
    start_date = params.get("start_date") or "jutro"
    end_date   = params.get("end_date") or "bez końca"
    has_files  = bool(files)
    has_copy   = bool(params.get("ad_copy"))
    link_ok    = bool(params.get("website_url")) and params.get("link_enabled", True)

    _obj_map = {
        "OUTCOME_TRAFFIC":       "Ruch na stronie",
        "OUTCOME_ENGAGEMENT":    "Zaangażowanie",
        "OUTCOME_LEADS":         "Pozyskanie leadów",
        "OUTCOME_SALES":         "Sprzedaż / Konwersje",
        "OUTCOME_AWARENESS":     "Zasięg / Świadomość",
        "OUTCOME_APP_PROMOTION": "Promocja aplikacji",
    }
    obj_friendly = _obj_map.get(objective, objective)

    campaign_ctx = (
        f"- Klient: *{client}*\n"
        f"- Cel kampanii: {obj_friendly}\n"
        f"- Budżet dzienny: {budget:.0f} PLN\n"
        f"- Szacowany tygodniowy: {budget * 7:.0f} PLN\n"
        f"- Płeć: {tgt.get('gender', 'all')}\n"
        f"- Wiek: {tgt.get('age_min', 18)}–{tgt.get('age_max', 65)} lat\n"
        f"- Lokalizacje: {', '.join(tgt.get('locations') or ['Polska'])}\n"
        f"- Zainteresowania: {', '.join(tgt.get('interests') or []) or 'brak (broad)'}\n"
        f"- Platformy: {', '.join(platform) if platform else 'automatyczne (Advantage+)'}\n"
        f"- Umiejscowienia: {', '.join(placements) if placements else 'automatyczne'}\n"
        f"- Kreacje: {'uploadowane (' + str(len(files)) + ' pliki)' if has_files else 'z istniejącego posta na stronie FB'}\n"
        f"- Copy: {'tak' if has_copy else 'brak (z posta FB)'}\n"
        f"- Link do strony: {'tak — ' + str(params.get('website_url')) if link_ok else 'brak / wyłączony'}\n"
        f"- CTA: {params.get('call_to_action') or 'LEARN_MORE'}\n"
        f"- Start: {start_date} → Koniec: {end_date}"
    )

    prompt = f"""Jesteś doświadczonym ekspertem performance marketingu specjalizującym się w Meta Ads (Facebook/Instagram). Pracujesz w agencji marketingowej i rozmawiasz z account managerem przez Slacka.

Przeanalizuj tę kampanię i odpowiedz bezpośrednio, po polsku, jak kolega-ekspert.

PARAMETRY KAMPANII:
{campaign_ctx}

---
FUNDAMENTY PERFORMANCE MARKETINGU — zawsze sprawdzaj każdy punkt i uwzględniaj w analizie:

📌 FAZA UCZENIA
- Meta potrzebuje min. 50 konwersji/tydzień żeby wyjść z fazy uczenia. Przy małym budżecie (<50 zł/dzień) kampania konwersyjna może tkwić w fazie uczenia przez cały czas trwania — rozważ OUTCOME_TRAFFIC lub OUTCOME_ENGAGEMENT zamiast konwersji.

📌 RETARGETING vs COLD AUDIENCE
- Cold audience (brak retargetingu) = zazwyczaj ROAS 1–2×. Retargeting (odwiedzający stronę, osoby zaangażowane w posty, widzowie video) = 3–7× ROAS. Jeśli brak grupy retargetingowej → zawsze zwróć na to uwagę i zaproponuj konkretne custom audiences.

📌 BROAD vs INTEREST TARGETING
- Wąskie zainteresowania (<50k reach) = mała skala, wysokie CPM. Szerokie zainteresowania + Advantage+ Audience = często skuteczniejsze przy dobrych kreacjach. Zbyt duża liczba zainteresowań = rozmyta grupa. Optymalnie: 1–3 tematycznie powiązane zainteresowania LUB broad.

📌 FORMAT KREACJI vs PLACEMENT
- Reels/Stories wymagają pionowego wideo 9:16. Feed Instagram/Facebook = kwadrat 1:1 lub poziomy 1.91:1. Zły format = przycięte kreacje = drastyczny spadek CTR. Jeśli kreacja jest foto na Reels → zapytaj o format.

📌 CZAS TRWANIA I OPTYMALIZACJA
- Kampania <7 dni = za mało danych do optymalizacji. Min. 7–14 dni żeby Meta zebrało dane i zoptymalizowało dostarczanie. Kampanie wieczne (bez daty końca) przy małym budżecie = ryzyko wchodzenia w "holiday pricing" bez kontroli.

📌 PIXEL / TRACKING
- Kampanie OUTCOME_SALES lub OUTCOME_LEADS bez Pixela = Meta optymalizuje w ciemno. Zawsze zapytaj: czy piksel jest zainstalowany? Czy zdarzenia (ViewContent, AddToCart, Purchase, Lead) są skonfigurowane?

📌 BUDŻET vs AUDIENCE SIZE
- Zbyt mały budżet na dużą grupę = niska częstotliwość = słabe wyniki. Zbyt duży budżet na małą grupę (<50k) = szybkie nasycenie, rosnące CPM i CPR. Złota proporcja: ~0,5–1 PLN dziennie na każde 1 000 osób w grupie docelowej.

📌 COPY I CTA
- Bez ad copy Meta używa tekstu z posta (może być nieoptymalne dla reklamy). Krótkie copy (<125 znaków) lepsze na mobile — dłuższe jest ucinane. CTA powinno pasować do celu: ruch → "Dowiedz się więcej", sprzedaż → "Kup teraz", lead → "Zarejestruj się".

📌 KREATYWNOŚĆ I A/B
- Jedna kreacja na zestaw = brak danych porównawczych. Optymalnie 2–4 kreacje w adset — Meta automatycznie preferuje lepiej performującą. Zapytaj o warianty jeśli jest tylko jedna kreacja.
---

Twoja odpowiedź powinna:
1. Jednym krótkim zdaniem potwierdzić co rozumiesz
2. Podać 2-4 najważniejsze uwagi na podstawie FUNDAMENTÓW powyżej — tylko te które faktycznie dotyczą TEJ kampanii i mogą realnie poprawić wyniki
3. NIE wymieniać fundamentów które są OK — tylko te które wymagają uwagi
4. Być konkretnym — zamiast "rozważ retargeting" napisz "dodaj retargeting osób z dre.pl z ostatnich 30 dni — zazwyczaj 3-5x lepszy ROAS niż cold audience"
5. Jeśli coś jest niejasne (np. czy piksel jest zainstalowany, jaki format kreacji) — zapytaj wprost

Format: Slack markdown (gwiazdki do bold, myślniki, emoji). Max 250 słów.

Na KOŃCU (osobna linia) napisz dosłownie:
"Napisz co chcesz zmienić lub potwierdź: *zaczynaj*"

Nie bądź formalny. Mów wprost."""

    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"generate_campaign_expert_analysis error: {e}")
        return ""


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
        "TRAFFIC":          "OUTCOME_TRAFFIC",
        "ENGAGEMENT":       "OUTCOME_ENGAGEMENT",
        "APP_INSTALLS":     "OUTCOME_APP_PROMOTION",
        "REACH":            "OUTCOME_AWARENESS",
        "BRAND_AWARENESS":  "OUTCOME_AWARENESS",
        "CONVERSIONS":      "OUTCOME_SALES",
        "LEAD_GENERATION":  "OUTCOME_LEADS",
        "VIDEO_VIEWS":      "OUTCOME_ENGAGEMENT",
        "POST_ENGAGEMENT":  "OUTCOME_ENGAGEMENT",
        "LINK_CLICKS":      "OUTCOME_TRAFFIC",
    }
    objective = _legacy_obj_map.get(objective, objective)

    opt_goal_map = {
        "OUTCOME_TRAFFIC":       "LINK_CLICKS",
        "OUTCOME_ENGAGEMENT":    "POST_ENGAGEMENT",
        "OUTCOME_LEADS":         "LEAD_GENERATION",
        "OUTCOME_SALES":         "OFFSITE_CONVERSIONS",
        "OUTCOME_AWARENESS":     "REACH",
        "OUTCOME_APP_PROMOTION": "APP_INSTALLS",
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

    # Umiejscowienie reklamy (placements) — jeśli user podał konkretne
    _pub_platforms = campaign_params.get("publisher_platforms")  # np. ["facebook","instagram"]
    _placements    = campaign_params.get("placement_positions")   # np. ["feed","story","reels"]
    if _pub_platforms:
        targeting_with_auto["publisher_platforms"] = _pub_platforms
        if _placements:
            # facebook_positions: feed, story, reels, video_feeds, search, marketplace
            _fb_pos_map = {"feed": "feed", "story": "story", "reels": "reels",
                           "video": "video_feeds", "search": "search", "marketplace": "marketplace"}
            # instagram_positions: stream (=feed), story, reels, explore
            _ig_pos_map = {"feed": "stream", "stream": "stream", "story": "story",
                           "reels": "reels", "explore": "explore"}
            if "facebook" in _pub_platforms:
                _fb_pos = [_fb_pos_map[p] for p in _placements if p in _fb_pos_map]
                if _fb_pos:
                    targeting_with_auto["facebook_positions"] = _fb_pos
            if "instagram" in _pub_platforms:
                _ig_pos = [_ig_pos_map[p] for p in _placements if p in _ig_pos_map]
                if _ig_pos:
                    targeting_with_auto["instagram_positions"] = _ig_pos
        logger.info(f"Placements: platforms={_pub_platforms} positions={_placements}")
    else:
        logger.info("Placements: automatic (advantage+)")

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
    ad_ids      = []
    website     = campaign_params.get("website_url") or "https://patoagencja.com"
    ad_copy     = campaign_params.get("ad_copy", "")
    cta         = campaign_params.get("call_to_action", "LEARN_MORE")
    cta_enabled  = campaign_params.get("cta_enabled", True)
    link_enabled = campaign_params.get("link_enabled", True)

    def _get_page_access_token(pid: str) -> str:
        """Pobiera Page Access Token z /me/accounts (User Token → Page Token)."""
        try:
            resp = requests.get(
                "https://graph.facebook.com/v19.0/me/accounts",
                params={"access_token": _token, "fields": "id,access_token", "limit": 50},
                timeout=15,
            )
            for page in resp.json().get("data", []):
                if page.get("id") == pid:
                    logger.info(f"Got page access token for page {pid}")
                    return page.get("access_token", "")
        except Exception as e:
            logger.warning(f"_get_page_access_token error: {e}")
        return ""

    def _get_existing_page_post(pid: str):
        # Page Access Token jest wymagany przez Meta API dla /published_posts
        page_token = _get_page_access_token(pid) or _token
        try:
            resp = requests.get(
                f"https://graph.facebook.com/v19.0/{pid}/published_posts",
                params={"access_token": page_token, "fields": "id", "limit": 5},
                timeout=15,
            )
            body = resp.json()
            logger.info(f"_get_existing_page_post: {json.dumps(body)[:300]}")
            data = body.get("data", [])
            if data:
                return data[0]["id"]
            if "error" in body:
                logger.warning(f"_get_existing_page_post error: {body['error'].get('message')}")
        except Exception as e:
            logger.warning(f"_get_existing_page_post exception: {e}")
        return None

    def _create_page_photo_post(pid: str, image_hash: str, message: str) -> str | None:
        """
        Tworzy post ze zdjęciem na stronie FB (dark post — published=false).
        Zwraca post_id do użycia jako object_story_id w reklamie.
        Omija błąd 1885183 (unverified business portfolio przy object_story_spec).
        """
        page_token = _get_page_access_token(pid) or _token
        try:
            resp = requests.post(
                f"https://graph.facebook.com/v19.0/{pid}/photos",
                params={"access_token": page_token},
                data={
                    "hash":      image_hash,
                    "message":   message or "",
                    "published": "false",
                },
                timeout=30,
            )
            body = resp.json()
            logger.info(f"_create_page_photo_post response: {json.dumps(body)[:300]}")
            # Przy published=false Meta zwraca post_id bezpośrednio
            post_id = body.get("post_id") or body.get("id")
            if post_id:
                return str(post_id)
            if "error" in body:
                logger.warning(f"_create_page_photo_post error: {body['error'].get('message')}")
        except Exception as e:
            logger.warning(f"_create_page_photo_post exception: {e}")
        return None

    def _discover_page_id() -> str:
        """Jeśli page_id nie ustawiony — szuka strony przez /me/accounts, dopasowuje po kliencie."""
        try:
            resp = requests.get(
                "https://graph.facebook.com/v19.0/me/accounts",
                params={"access_token": _token, "fields": "id,name", "limit": 20},
                timeout=15,
            )
            pages = resp.json().get("data", [])
            logger.info(f"_discover_page_id found {len(pages)} pages: {[p.get('name') for p in pages]}")
            if not pages:
                return ""
            # Szukaj po nazwie klienta żeby nie brać przypadkowo złej strony
            _client_l = (campaign_params.get("client_name") or "").lower()
            _keywords = {
                "tc2023":  ("timecatcher", "tc2023", "tc 2023"),
                "dre":     ("dre", "doors"),
                "instax":  ("instax", "fuji"),
                "m2":      ("m2",),
                "pato":    ("pato",),
            }
            for kws in _keywords.get(_client_l, ()):
                for p in pages:
                    if kws in (p.get("name") or "").lower():
                        return p["id"]
            return pages[0]["id"]
        except Exception as e:
            logger.warning(f"_discover_page_id error: {e}")
        return ""

    # Jeśli page_id nie skonfigurowany — próbuj auto-discovery
    if not page_id:
        page_id = _discover_page_id()
        if page_id:
            logger.info(f"Auto-discovered page_id: {page_id}")

    existing_post_id = _get_existing_page_post(page_id) if page_id else None
    if existing_post_id:
        logger.info(f"Using existing page post: {existing_post_id}")
    else:
        logger.warning(f"No existing page post found (page_id={page_id!r}) — no ads will be created")

    # Jeśli brak uploadowanych kreacji ale jest post na stronie — stwórz 1 reklamę z posta
    if not creatives and existing_post_id:
        creatives = [{"type": "page_post", "hash": None, "id": None}]

    for i, creative in enumerate(creatives):
        try:
            cr_type = creative.get("type")

            if cr_type == "image":
                # Własny obrazek → stwórz dark post na stronie → object_story_id
                # (omija błąd 1885183 przy object_story_spec)
                own_post_id = None
                if page_id and creative.get("hash"):
                    own_post_id = _create_page_photo_post(page_id, creative["hash"], ad_copy or "")
                if own_post_id:
                    logger.info(f"Using own image post: {own_post_id}")
                    creative_payload = {
                        "name":            f"{campaign_params['campaign_name']} - Creative {i+1}",
                        "object_story_id": own_post_id,
                    }
                else:
                    # Fallback: object_story_spec z image_hash
                    logger.warning("_create_page_photo_post failed — falling back to object_story_spec")
                    _link_data: dict = {
                        "image_hash": creative["hash"],
                        "message":    ad_copy,
                    }
                    if link_enabled:
                        _link_data["link"] = website
                    if cta_enabled and link_enabled:
                        _link_data["call_to_action"] = {"type": cta, "value": {"link": website}}
                    story_spec = {"link_data": _link_data}
                    if page_id:
                        story_spec["page_id"] = page_id
                    creative_payload = {
                        "name":              f"{campaign_params['campaign_name']} - Creative {i+1}",
                        "object_story_spec": story_spec,
                    }

            elif cr_type == "video":
                # Video → object_story_spec z video_data
                _video_data: dict = {
                    "video_id": creative["id"],
                    "title":    campaign_params["campaign_name"],
                    "message":  ad_copy,
                }
                if cta_enabled and link_enabled:
                    _video_data["call_to_action"] = {"type": cta, "value": {"link": website}}
                story_spec = {"video_data": _video_data}
                if page_id:
                    story_spec["page_id"] = page_id
                creative_payload = {
                    "name":              f"{campaign_params['campaign_name']} - Creative {i+1}",
                    "object_story_spec": story_spec,
                }

            else:
                # Brak własnej kreacji (type="page_post") → istniejący post ze strony
                creative_payload = {
                    "name":            f"{campaign_params['campaign_name']} - Creative {i+1}",
                    "object_story_id": existing_post_id,
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
        "campaign_id":     campaign_id,
        "adset_id":        adset_id,
        "ad_ids":          ad_ids,
        "params":          campaign_params,
        "account_id":      account_id,
        "using_page_post": bool(existing_post_id),
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

    budget      = float(campaign_params.get("daily_budget") or 0)
    total_7d    = budget * 7
    start       = campaign_params.get("start_date", "—")
    end         = campaign_params.get("end_date") or "Bez końca"
    client      = (campaign_params.get("client_name") or "").upper()
    camp_name   = campaign_params.get("campaign_name", "—")
    ad_copy     = campaign_params.get("ad_copy") or None
    url         = campaign_params.get("website_url") or None
    cta         = campaign_params.get("call_to_action", "LEARN_MORE")
    cta_enabled  = campaign_params.get("cta_enabled", True)
    link_enabled = campaign_params.get("link_enabled", True)
    camp_id     = draft_ids["campaign_id"]
    using_post  = draft_ids.get("using_page_post", False)

    # Umiejscowienie
    _pub = campaign_params.get("publisher_platforms")
    _pos = campaign_params.get("placement_positions")
    if _pub:
        _plat_str = " + ".join(p.capitalize() for p in _pub)
        _pos_str  = " | ".join(p.capitalize() for p in (_pos or [])) if _pos else "Wszystkie"
        placement_line = f"🖥️ *UMIEJSCOWIENIE:* {_plat_str} — {_pos_str}\n"
    else:
        placement_line = "🖥️ *UMIEJSCOWIENIE:* Automatyczne (Advantage+)\n"

    kreacje_str = (
        "📌 Istniejący post ze strony FB"
        if using_post
        else f"{creative_count} szt. (uploadowane)"
    )
    copy_line   = f"📝 *COPY:*\n_{ad_copy}_\n\n" if ad_copy else "📝 *COPY:* _(z posta FB)_\n\n"
    link_line   = f"🔗 *Link:* {url}\n" if link_enabled and url else ("🔗 *Link:* _(brak)_\n" if not link_enabled else "")
    cta_line    = f"📣 *CTA:* {cta}\n" if cta_enabled else "📣 *CTA:* _(wyłączony)_\n"

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
        f"{placement_line}\n"
        f"🎨 *KREACJE:* {kreacje_str}\n\n"
        f"{copy_line}"
        f"{link_line}"
        f"{cta_line}\n"
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
