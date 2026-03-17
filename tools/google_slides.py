"""Google Slides integration — tworzy prezentacje przez Slides API."""
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    _SLIDES_AVAILABLE = True
except ImportError:
    _SLIDES_AVAILABLE = False
    logger.warning("google-api-python-client nie zainstalowany — Google Slides niedostępny")

GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_SLIDES_CLIENT_ID") or os.environ.get("GOOGLE_ADS_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_SLIDES_CLIENT_SECRET") or os.environ.get("GOOGLE_ADS_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_SLIDES_REFRESH_TOKEN")


def _get_services():
    if not _SLIDES_AVAILABLE:
        raise RuntimeError("Biblioteka google-api-python-client nie jest zainstalowana.")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REFRESH_TOKEN:
        raise RuntimeError(
            "Brak GOOGLE_SLIDES_REFRESH_TOKEN (lub GOOGLE_ADS_CLIENT_ID/CLIENT_SECRET) w zmiennych środowiskowych."
        )
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/presentations",
            "https://www.googleapis.com/auth/drive.file",
        ],
    )
    slides_svc = build("slides", "v1", credentials=creds, cache_discovery=False)
    drive_svc  = build("drive",  "v3", credentials=creds, cache_discovery=False)
    return slides_svc, drive_svc


def _rgb(r, g, b):
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


def _pt(val):
    return {"magnitude": val, "unit": "PT"}


def _add_text_box(requests, slide_id, text, x, y, w, h,
                  font_size=14, bold=False, color=None, align="LEFT"):
    box_id = f"box_{slide_id}_{x}_{y}"
    requests.append({
        "createShape": {
            "objectId": box_id,
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {"width": _pt(w), "height": _pt(h)},
                "transform": {
                    "scaleX": 1, "scaleY": 1,
                    "translateX": x, "translateY": y,
                    "unit": "PT",
                },
            },
        }
    })
    requests.append({
        "insertText": {"objectId": box_id, "text": text, "insertionIndex": 0}
    })
    style = {"fontSize": _pt(font_size), "bold": bold}
    if color:
        style["foregroundColor"] = {"opaqueColor": {"rgbColor": _rgb(*color)}}
    requests.append({
        "updateTextStyle": {
            "objectId": box_id,
            "textRange": {"type": "ALL"},
            "style": style,
            "fields": "fontSize,bold" + (",foregroundColor" if color else ""),
        }
    })
    requests.append({
        "updateParagraphStyle": {
            "objectId": box_id,
            "textRange": {"type": "ALL"},
            "style": {"alignment": align},
            "fields": "alignment",
        }
    })
    return box_id


def _set_slide_bg(requests, slide_id, r, g, b):
    requests.append({
        "updatePageProperties": {
            "objectId": slide_id,
            "pageProperties": {
                "pageBackgroundFill": {
                    "solidFill": {"color": {"rgbColor": _rgb(r, g, b)}}
                }
            },
            "fields": "pageBackgroundFill",
        }
    })


# ── Publiczne API ───────────────────────────────────────────────────────────────

def create_presentation(
    title: str,
    client_name: str = None,
    subtitle: str = None,
    google_ads_data: dict = None,
    meta_ads_data: dict = None,
    brief: str = None,
    date_range: str = None,
    extra_slides: list = None,
) -> dict:
    """
    Tworzy prezentację w Google Slides i zwraca link.

    Parametry:
      title           — tytuł prezentacji (np. "Oferta dla OLX")
      client_name     — nazwa klienta
      subtitle        — podtytuł / tagline
      google_ads_data — dict z wynikami Google Ads (opcjonalnie)
      meta_ads_data   — dict z wynikami Meta Ads (opcjonalnie)
      brief           — tekst briefu / opis oferty (opcjonalnie)
      date_range      — zakres dat np. "01.03.2026 – 31.03.2026"
      extra_slides    — lista dict {"title": str, "content": str} dodatkowych slajdów

    Zwraca: {"url": str, "presentation_id": str} lub {"error": str}
    """
    try:
        slides_svc, drive_svc = _get_services()
    except RuntimeError as e:
        return {"error": str(e)}

    try:
        today = datetime.now().strftime("%d.%m.%Y")
        pres_title = f"{title} — {today}"

        # Utwórz pustą prezentację
        pres = slides_svc.presentations().create(
            body={"title": pres_title}
        ).execute()
        pres_id = pres["presentationId"]

        # Pobierz ID pierwszego slajdu (tworzonego automatycznie)
        first_slide_id = pres["slides"][0]["objectId"]

        requests = []

        # ── SLAJD 1: Tytuł ──────────────────────────────────────────────────
        slide1_id = first_slide_id
        _set_slide_bg(requests, slide1_id, 15, 15, 35)  # ciemnogranatowy

        # Usuń domyślne elementy slajdu tytułowego
        for elem in pres["slides"][0].get("pageElements", []):
            requests.append({"deleteObject": {"objectId": elem["objectId"]}})

        _add_text_box(requests, slide1_id, title,
                      x=50, y=150, w=560, h=100,
                      font_size=36, bold=True, color=(255, 255, 255), align="CENTER")

        sub = subtitle or (f"Klient: {client_name}" if client_name else "")
        if sub:
            _add_text_box(requests, slide1_id, sub,
                          x=50, y=270, w=560, h=50,
                          font_size=20, color=(180, 180, 255), align="CENTER")

        dr = date_range or today
        _add_text_box(requests, slide1_id, dr,
                      x=50, y=340, w=560, h=30,
                      font_size=13, color=(150, 150, 200), align="CENTER")

        # ── SLAJD 2: Brief / Opis oferty (jeśli podano) ─────────────────────
        if brief:
            brief_slide = {"insertSlide": {"insertionIndex": 1, "slideLayoutReference": {"predefinedLayout": "BLANK"}}}
            requests.append(brief_slide)
            brief_id = f"slide_brief"
            requests.append({"objectId": brief_id} if False else {})  # placeholder — faktyczne ID pobierzemy po execute

        # Wykonaj wszystko co mamy do tej pory (żeby uzyskać ID nowych slajdów)
        if requests:
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={"requests": [r for r in requests if r]}
            ).execute()
        requests = []

        # Pobierz aktualny stan prezentacji
        pres_state = slides_svc.presentations().get(presentationId=pres_id).execute()
        existing_slides = pres_state["slides"]

        def _new_slide(idx):
            """Dodaje nowy slajd i zwraca jego ID po execute."""
            _req = [{"insertSlide": {"insertionIndex": idx, "slideLayoutReference": {"predefinedLayout": "BLANK"}}}]
            res = slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={"requests": _req}
            ).execute()
            refreshed = slides_svc.presentations().get(presentationId=pres_id).execute()
            return refreshed["slides"][idx]["objectId"]

        slide_idx = 1

        # ── SLAJD 2: Brief ───────────────────────────────────────────────────
        if brief:
            sid = _new_slide(slide_idx)
            slide_idx += 1
            reqs = []
            _set_slide_bg(reqs, sid, 245, 245, 252)
            _add_text_box(reqs, sid, "Brief / Opis", x=40, y=30, w=500, h=40,
                          font_size=24, bold=True, color=(15, 15, 80))
            _add_text_box(reqs, sid, brief[:800], x=40, y=90, w=540, h=330,
                          font_size=13, color=(30, 30, 60))
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id, body={"requests": reqs}
            ).execute()

        # ── SLAJD: Google Ads ────────────────────────────────────────────────
        if google_ads_data and not google_ads_data.get("error"):
            sid = _new_slide(slide_idx)
            slide_idx += 1
            reqs = []
            _set_slide_bg(reqs, sid, 255, 255, 255)
            _add_text_box(reqs, sid, "Google Ads", x=40, y=20, w=300, h=45,
                          font_size=26, bold=True, color=(66, 133, 244))

            rows = []
            campaigns = google_ads_data.get("campaigns") or google_ads_data.get("results") or []
            if campaigns:
                for c in campaigns[:8]:
                    name   = c.get("campaign_name") or c.get("name", "—")
                    impr   = c.get("impressions", 0)
                    clicks = c.get("clicks", 0)
                    cost   = c.get("cost", 0)
                    conv   = c.get("conversions", 0)
                    rows.append(f"• {name[:35]}: {impr:,} wyśw. | {clicks:,} kliknięć | {cost:.2f} PLN | {conv:.0f} konw.")
            else:
                for k, v in google_ads_data.items():
                    if k != "error":
                        rows.append(f"• {k}: {v}")

            content = "\n".join(rows) if rows else "Brak danych"
            _add_text_box(reqs, sid, content, x=40, y=80, w=560, h=300,
                          font_size=12, color=(30, 30, 30))
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id, body={"requests": reqs}
            ).execute()

        # ── SLAJD: Meta Ads ──────────────────────────────────────────────────
        if meta_ads_data and not meta_ads_data.get("error"):
            sid = _new_slide(slide_idx)
            slide_idx += 1
            reqs = []
            _set_slide_bg(reqs, sid, 255, 255, 255)
            _add_text_box(reqs, sid, "Meta Ads", x=40, y=20, w=300, h=45,
                          font_size=26, bold=True, color=(66, 103, 178))

            rows = []
            campaigns = meta_ads_data.get("campaigns") or meta_ads_data.get("results") or []
            if campaigns:
                for c in campaigns[:8]:
                    name    = c.get("campaign_name") or c.get("name", "—")
                    impr    = c.get("impressions", 0)
                    clicks  = c.get("clicks", 0)
                    spend   = c.get("spend", 0)
                    results = c.get("results", 0)
                    rows.append(f"• {name[:35]}: {impr:,} wyśw. | {clicks:,} kliknięć | {spend:.2f} PLN | {results} wyniki")
            else:
                for k, v in meta_ads_data.items():
                    if k not in ("error", "status"):
                        rows.append(f"• {k}: {v}")

            content = "\n".join(rows) if rows else "Brak danych"
            _add_text_box(reqs, sid, content, x=40, y=80, w=560, h=300,
                          font_size=12, color=(30, 30, 30))
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id, body={"requests": reqs}
            ).execute()

        # ── Dodatkowe slajdy (wolna treść) ───────────────────────────────────
        for extra in (extra_slides or []):
            sid = _new_slide(slide_idx)
            slide_idx += 1
            reqs = []
            _set_slide_bg(reqs, sid, 250, 250, 255)
            _add_text_box(reqs, sid, extra.get("title", ""), x=40, y=20, w=560, h=50,
                          font_size=24, bold=True, color=(15, 15, 80))
            _add_text_box(reqs, sid, extra.get("content", ""), x=40, y=85, w=560, h=320,
                          font_size=13, color=(30, 30, 60))
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id, body={"requests": reqs}
            ).execute()

        url = f"https://docs.google.com/presentation/d/{pres_id}/edit"
        logger.info(f"Prezentacja utworzona: {url}")
        return {"url": url, "presentation_id": pres_id, "title": pres_title}

    except Exception as e:
        logger.error(f"Błąd tworzenia prezentacji Google Slides: {e}", exc_info=True)
        return {"error": str(e)}
