"""Google Slides integration — profesjonalne prezentacje przez Slides API."""
import os
import uuid
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

# Slide canvas: 720 × 405 pt (16:9)
W, H = 720, 405

# Brand palette
C_PURPLE  = (123, 97, 255)
C_PINK    = (255, 77, 139)
C_ORANGE  = (255, 107, 53)
C_TEAL    = (0, 191, 165)
C_NAVY    = (18, 18, 48)
C_WHITE   = (255, 255, 255)
C_LGRAY   = (245, 245, 250)
C_DGRAY   = (80, 80, 100)
C_TEXT    = (20, 20, 40)

ACCENT_COLORS = [C_PURPLE, C_PINK, C_ORANGE, C_TEAL, (255, 184, 0)]


def _get_services():
    if not _SLIDES_AVAILABLE:
        raise RuntimeError("Biblioteka google-api-python-client nie jest zainstalowana.")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REFRESH_TOKEN:
        raise RuntimeError("Brak GOOGLE_SLIDES_REFRESH_TOKEN w zmiennych środowiskowych.")
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


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _id():
    return "obj_" + uuid.uuid4().hex[:12]


def _pt(v):
    return {"magnitude": v, "unit": "PT"}


def _rgb(r, g, b):
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


def _solid(r, g, b, alpha=1.0):
    return {"solidFill": {"color": {"rgbColor": _rgb(r, g, b)}, "alpha": alpha}}


def _transform(x, y, sx=1, sy=1):
    return {"scaleX": sx, "scaleY": sy, "translateX": x, "translateY": y, "unit": "PT"}


def _rect(oid, slide_id, x, y, w, h, color, alpha=1.0, shape="RECTANGLE"):
    return [
        {"createShape": {
            "objectId": oid,
            "shapeType": shape,
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {"width": _pt(w), "height": _pt(h)},
                "transform": _transform(x, y),
            },
        }},
        {"updateShapeProperties": {
            "objectId": oid,
            "shapeProperties": {
                "shapeBackgroundFill": _solid(*color, alpha),
                "outline": {"propertyState": "NOT_RENDERED"},
            },
            "fields": "shapeBackgroundFill,outline",
        }},
    ]


def _text_box(oid, slide_id, text, x, y, w, h,
              size=14, bold=False, color=C_TEXT, align="LEFT", valign="TOP"):
    halign_map = {"LEFT": "START", "CENTER": "CENTER", "RIGHT": "END"}
    return [
        {"createShape": {
            "objectId": oid,
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {"width": _pt(w), "height": _pt(h)},
                "transform": _transform(x, y),
            },
        }},
        {"insertText": {"objectId": oid, "text": str(text)}},
        {"updateTextStyle": {
            "objectId": oid,
            "style": {
                "fontSize": _pt(size),
                "bold": bold,
                "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(*color)}},
                "fontFamily": "Google Sans",
            },
            "fields": "fontSize,bold,foregroundColor,fontFamily",
        }},
        {"updateParagraphStyle": {
            "objectId": oid,
            "style": {
                "alignment": halign_map.get(align, "START"),
                "spaceAbove": _pt(0),
                "spaceBelow": _pt(0),
            },
            "fields": "alignment,spaceAbove,spaceBelow",
        }},
    ]


def _batch(svc, pres_id, reqs):
    if reqs:
        svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={"requests": [r for r in reqs if r]}
        ).execute()


def _add_slide(svc, pres_id, idx):
    _batch(svc, pres_id, [{"insertSlide": {
        "insertionIndex": idx,
        "slideLayoutReference": {"predefinedLayout": "BLANK"},
    }}])
    p = svc.presentations().get(presentationId=pres_id).execute()
    return p["slides"][idx]["objectId"]


# ── Slide builders ────────────────────────────────────────────────────────────

def _slide_title(svc, pres_id, slide_id, title, subtitle, client, date_range, channels):
    reqs = []

    # Delete default elements
    p = svc.presentations().get(presentationId=pres_id).execute()
    for s in p["slides"]:
        if s["objectId"] == slide_id:
            for el in s.get("pageElements", []):
                reqs.append({"deleteObject": {"objectId": el["objectId"]}})

    # White background
    reqs += _rect(_id(), slide_id, 0, 0, W, H, C_WHITE)

    # Top accent bar (3 segments: purple / pink / orange)
    reqs += _rect(_id(), slide_id, 0,   0, 200, 5, C_PURPLE)
    reqs += _rect(_id(), slide_id, 200, 0, 200, 5, C_PINK)
    reqs += _rect(_id(), slide_id, 400, 0, 320, 5, C_ORANGE)

    # Decorative circles (right side)
    reqs += _rect(_id(), slide_id, 480, 40,  280, 280, C_PURPLE, 0.08, "ELLIPSE")
    reqs += _rect(_id(), slide_id, 520, 130, 220, 220, C_PINK,   0.10, "ELLIPSE")
    reqs += _rect(_id(), slide_id, 580, 200, 160, 160, C_ORANGE, 0.08, "ELLIPSE")

    # Title
    reqs += _text_box(_id(), slide_id, title.upper(),
                      40, 40, 460, 140, size=52, bold=True, color=C_TEXT)

    # Subtitle (purple)
    if subtitle:
        reqs += _text_box(_id(), slide_id, subtitle,
                          40, 185, 400, 35, size=16, bold=True, color=C_PURPLE)

    # "Post-Buy Report" label
    reqs += _text_box(_id(), slide_id, "Post-Buy Report",
                      40, 220, 300, 25, size=13, color=C_DGRAY)

    # Colored divider line
    reqs += _rect(_id(), slide_id, 40, 250, 60, 3, C_PURPLE)
    reqs += _rect(_id(), slide_id, 105, 250, 40, 3, C_PINK)
    reqs += _rect(_id(), slide_id, 150, 250, 50, 3, C_ORANGE)

    # Metadata rows
    y = 265
    if client:
        reqs += _text_box(_id(), slide_id, f"Klient:  {client}",
                          40, y, 400, 20, size=11, color=C_DGRAY)
        y += 22
    reqs += _text_box(_id(), slide_id, "Agencja:  patoagencja",
                      40, y, 400, 20, size=11, color=C_DGRAY)
    y += 22
    if channels:
        reqs += _text_box(_id(), slide_id, f"Kanały:  {channels}",
                          40, y, 500, 20, size=11, color=C_DGRAY)
        y += 22
    if date_range:
        reqs += _text_box(_id(), slide_id, f"Okres:  {date_range}",
                          40, y, 400, 20, size=11, bold=True, color=C_TEXT)

    # Bottom footer
    reqs += _rect(_id(), slide_id, 0, H - 22, W, 22, C_NAVY)
    footer = f"{title.upper()}  ·  PATO AGENCJA  ·  POST-BUY REPORT"
    reqs += _text_box(_id(), slide_id, footer,
                      20, H - 19, W - 40, 16, size=7, color=C_WHITE, align="LEFT")

    _batch(svc, pres_id, reqs)


def _slide_kpis(svc, pres_id, slide_id, kpis: list, heading="Wyniki w liczbach."):
    """kpis = [{"label": "Zasięg", "value": "93 634", "sub": "os."}, ...]"""
    reqs = []
    reqs += _rect(_id(), slide_id, 0, 0, W, H, C_WHITE)

    # Top bar
    reqs += _rect(_id(), slide_id, 0,   0, 200, 5, C_PURPLE)
    reqs += _rect(_id(), slide_id, 200, 0, 200, 5, C_PINK)
    reqs += _rect(_id(), slide_id, 400, 0, 320, 5, C_ORANGE)

    # Heading
    reqs += _text_box(_id(), slide_id, heading,
                      40, 20, 640, 60, size=32, bold=True, color=C_TEXT)
    reqs += _text_box(_id(), slide_id, "Podsumowanie kluczowych metryk kampanii",
                      40, 82, 640, 20, size=11, color=C_DGRAY)

    # KPI cards
    n = min(len(kpis), 5)
    card_w = (W - 80 - (n - 1) * 14) / n
    for i, kpi in enumerate(kpis[:5]):
        cx = 40 + i * (card_w + 14)
        cy = 115
        ch = 210
        color = ACCENT_COLORS[i % len(ACCENT_COLORS)]

        # Card background (light)
        reqs += _rect(_id(), slide_id, cx, cy, card_w, ch, color, 0.07)
        # Top accent strip
        reqs += _rect(_id(), slide_id, cx, cy, card_w, 4, color)
        # Big value
        reqs += _text_box(_id(), slide_id, str(kpi.get("value", "—")),
                          cx + 10, cy + 18, card_w - 20, 70,
                          size=34, bold=True, color=color, align="LEFT")
        # Sub-unit
        if kpi.get("sub"):
            reqs += _text_box(_id(), slide_id, kpi["sub"],
                              cx + 10, cy + 90, card_w - 20, 22,
                              size=11, color=C_DGRAY)
        # Label
        reqs += _text_box(_id(), slide_id, kpi.get("label", ""),
                          cx + 10, cy + 118, card_w - 20, 30,
                          size=10, bold=True, color=C_TEXT)
        # Delta/context
        if kpi.get("delta"):
            reqs += _text_box(_id(), slide_id, kpi["delta"],
                              cx + 10, cy + 150, card_w - 20, 22,
                              size=9, color=C_DGRAY)

    # Bottom footer
    reqs += _rect(_id(), slide_id, 0, H - 22, W, 22, C_NAVY)

    _batch(svc, pres_id, reqs)


def _slide_breakdown(svc, pres_id, slide_id, heading, rows: list, color=C_PURPLE):
    """rows = [{"name": str, "spend": str, "impressions": str, "clicks": str, "ctr": str, ...}]"""
    reqs = []
    reqs += _rect(_id(), slide_id, 0, 0, W, H, C_WHITE)
    reqs += _rect(_id(), slide_id, 0, 0, W, 5, color, 1.0)

    # Heading
    reqs += _text_box(_id(), slide_id, heading,
                      40, 18, 640, 45, size=26, bold=True, color=C_TEXT)

    # Table header bg
    reqs += _rect(_id(), slide_id, 30, 72, W - 60, 24, color, 0.12)

    cols = [
        ("Kampania",      200, 12),
        ("Spend",          80, 11),
        ("Wyświetlenia",   95, 11),
        ("Kliknięcia",     80, 11),
        ("CTR",            60, 11),
        ("CPM",            65, 11),
        ("Zasięg",         80, 11),
    ]
    # Header row
    x = 35
    for col_name, col_w, _ in cols:
        reqs += _text_box(_id(), slide_id, col_name, x, 74, col_w, 18,
                          size=9, bold=True, color=color)
        x += col_w + 4

    # Data rows
    for ri, row in enumerate(rows[:7]):
        ry = 100 + ri * 38
        if ri % 2 == 0:
            reqs += _rect(_id(), slide_id, 30, ry - 2, W - 60, 36, C_LGRAY)
        x = 35
        vals = [
            row.get("campaign_name") or row.get("name", "—"),
            _fmt_spend(row.get("spend")),
            _fmt_num(row.get("impressions")),
            _fmt_num(row.get("clicks")),
            _fmt_pct(row.get("ctr")),
            _fmt_spend(row.get("cpm")),
            _fmt_num(row.get("reach")),
        ]
        for vi, (_, col_w, fs) in enumerate(cols):
            txt = str(vals[vi] or "—")
            reqs += _text_box(_id(), slide_id, txt, x, ry + 2, col_w, 28,
                              size=fs, color=C_TEXT)
            x += col_w + 4

    reqs += _rect(_id(), slide_id, 0, H - 22, W, 22, C_NAVY)
    _batch(svc, pres_id, reqs)


def _slide_insights(svc, pres_id, slide_id, insights_text: str, heading="Wnioski & Rekomendacje"):
    reqs = []
    reqs += _rect(_id(), slide_id, 0, 0, W, H, C_NAVY)

    # Accent bar top
    reqs += _rect(_id(), slide_id, 0,   0, 200, 5, C_PURPLE)
    reqs += _rect(_id(), slide_id, 200, 0, 200, 5, C_PINK)
    reqs += _rect(_id(), slide_id, 400, 0, 320, 5, C_ORANGE)

    # Decorative circle
    reqs += _rect(_id(), slide_id, 500, 50, 280, 280, C_PURPLE, 0.1, "ELLIPSE")

    # Heading
    reqs += _text_box(_id(), slide_id, heading,
                      40, 25, 580, 55, size=28, bold=True, color=C_WHITE)
    reqs += _rect(_id(), slide_id, 40, 82, 80, 3, C_PINK)

    # Content — split into bullet points
    lines = [l.strip() for l in insights_text.strip().split("\n") if l.strip()]
    y = 100
    for line in lines[:8]:
        bullet = "•  " + line.lstrip("•-– ").strip()
        reqs += _text_box(_id(), slide_id, bullet, 40, y, 620, 30,
                          size=12, color=(220, 220, 255))
        y += 34

    reqs += _rect(_id(), slide_id, 0, H - 22, W, 22, C_PURPLE)
    _batch(svc, pres_id, reqs)


def _slide_free(svc, pres_id, slide_id, title: str, content: str, color=C_PURPLE):
    reqs = []
    reqs += _rect(_id(), slide_id, 0, 0, W, H, C_WHITE)
    reqs += _rect(_id(), slide_id, 0, 0, W, 5, color)

    reqs += _text_box(_id(), slide_id, title,
                      40, 18, 640, 50, size=28, bold=True, color=C_TEXT)
    reqs += _rect(_id(), slide_id, 40, 72, 60, 3, color)

    reqs += _text_box(_id(), slide_id, content,
                      40, 88, 640, 290, size=13, color=C_TEXT)

    reqs += _rect(_id(), slide_id, 0, H - 22, W, 22, C_NAVY)
    _batch(svc, pres_id, reqs)


# ── Number formatters ─────────────────────────────────────────────────────────

def _fmt_num(v):
    try:
        n = int(float(str(v).replace(",", ".")))
        if n >= 1_000_000:
            return f"{n/1_000_000:.2f} mln"
        if n >= 1_000:
            return f"{n:,}".replace(",", " ")
        return str(n)
    except Exception:
        return str(v) if v else "—"


def _fmt_spend(v):
    try:
        return f"{float(str(v).replace(',', '.')):.2f} zł"
    except Exception:
        return str(v) if v else "—"


def _fmt_pct(v):
    try:
        return f"{float(str(v).replace(',', '.')):.2f}%"
    except Exception:
        return str(v) if v else "—"


# ── KPI extraction from Meta Ads data ────────────────────────────────────────

def _extract_kpis(meta_data: dict) -> list:
    """Wyciąga top KPIs z meta_ads_data do kart na slajdzie."""
    rows = (meta_data or {}).get("data") or []
    if not rows:
        return []

    totals = {"spend": 0, "impressions": 0, "clicks": 0, "reach": 0, "conversions": 0}
    for r in rows:
        for k in totals:
            try:
                totals[k] += float(str(r.get(k, 0)).replace(",", "."))
            except Exception:
                pass

    ctr = (totals["clicks"] / totals["impressions"] * 100) if totals["impressions"] else 0
    cpm = (totals["spend"] / totals["impressions"] * 1000) if totals["impressions"] else 0
    cpc = (totals["spend"] / totals["clicks"]) if totals["clicks"] else 0

    kpis = [
        {"label": "Zasięg",        "value": _fmt_num(totals["reach"]),       "sub": "osób",    "delta": "unique reach"},
        {"label": "Wyświetlenia",  "value": _fmt_num(totals["impressions"]),  "sub": "impresji", "delta": ""},
        {"label": "Wydatki",       "value": _fmt_spend(totals["spend"]),      "sub": "",         "delta": "total spend"},
        {"label": "Kliknięcia",    "value": _fmt_num(totals["clicks"]),       "sub": "kliknięć", "delta": f"CTR {ctr:.2f}%"},
        {"label": "CPM",           "value": _fmt_spend(cpm),                  "sub": "/1000",    "delta": f"CPC {_fmt_spend(cpc)}"},
    ]
    return kpis


# ── Publiczne API ─────────────────────────────────────────────────────────────

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
    Tworzy profesjonalną prezentację w Google Slides.
    Parametry:
      title           — tytuł prezentacji
      client_name     — nazwa klienta
      subtitle        — podtytuł (np. "Kampania Digital")
      meta_ads_data   — dict z get_meta_ads_data()
      google_ads_data — dict z get_google_ads_data()
      brief           — opis / brief
      date_range      — "01.04 – 09.04.2026"
      extra_slides    — [{"title": str, "content": str}, ...]
                        ostatni element może mieć type="insights" dla dark slide
    Zwraca: {"url": str, "presentation_id": str}
    """
    try:
        slides_svc, _ = _get_services()
    except RuntimeError as e:
        return {"error": str(e)}

    try:
        today = datetime.now().strftime("%d.%m.%Y")
        pres_title = f"{title} — {today}"

        # Utwórz prezentację
        pres = slides_svc.presentations().create(body={"title": pres_title}).execute()
        pres_id = pres["presentationId"]
        first_slide_id = pres["slides"][0]["objectId"]

        idx = 0  # slajd index (first already exists)

        # ── Slajd 1: Tytuł ───────────────────────────────────────────────────
        channels = "Meta Ads"
        if google_ads_data and not google_ads_data.get("error"):
            channels += " · Google Ads"
        _slide_title(slides_svc, pres_id, first_slide_id,
                     title=title,
                     subtitle=subtitle,
                     client=client_name,
                     date_range=date_range or today,
                     channels=channels)
        idx = 1

        # ── Slajd 2: KPI summary (Meta) ──────────────────────────────────────
        if meta_ads_data and not meta_ads_data.get("error"):
            kpis = _extract_kpis(meta_ads_data)
            if kpis:
                sid = _add_slide(slides_svc, pres_id, idx); idx += 1
                _slide_kpis(slides_svc, pres_id, sid, kpis)

        # ── Slajd 3: Meta Ads breakdown ───────────────────────────────────────
        if meta_ads_data and not meta_ads_data.get("error"):
            rows = meta_ads_data.get("data") or []
            if rows:
                sid = _add_slide(slides_svc, pres_id, idx); idx += 1
                _slide_breakdown(slides_svc, pres_id, sid,
                                 "Meta Ads — Wyniki kampanii",
                                 rows, color=C_PURPLE)

        # ── Slajd 4: Google Ads breakdown ─────────────────────────────────────
        if google_ads_data and not google_ads_data.get("error"):
            rows = google_ads_data.get("campaigns") or google_ads_data.get("results") or []
            if rows:
                sid = _add_slide(slides_svc, pres_id, idx); idx += 1
                _slide_breakdown(slides_svc, pres_id, sid,
                                 "Google Ads — Wyniki kampanii",
                                 rows, color=(66, 133, 244))

        # ── Brief ─────────────────────────────────────────────────────────────
        if brief:
            sid = _add_slide(slides_svc, pres_id, idx); idx += 1
            _slide_free(slides_svc, pres_id, sid, "Brief & Cel kampanii", brief)

        # ── Extra slajdy ──────────────────────────────────────────────────────
        for extra in (extra_slides or []):
            sid = _add_slide(slides_svc, pres_id, idx); idx += 1
            if extra.get("type") == "insights":
                _slide_insights(slides_svc, pres_id, sid,
                                extra.get("content", ""),
                                extra.get("title", "Wnioski & Rekomendacje"))
            else:
                # Detect if it's insights-like content (last "dark" slide)
                t = extra.get("title", "")
                if any(w in t.lower() for w in ("wnios", "rekomen", "podsumow", "next", "plan")):
                    _slide_insights(slides_svc, pres_id, sid,
                                    extra.get("content", ""), t)
                else:
                    color = ACCENT_COLORS[idx % len(ACCENT_COLORS)]
                    _slide_free(slides_svc, pres_id, sid,
                                t, extra.get("content", ""), color=color)

        url = f"https://docs.google.com/presentation/d/{pres_id}/edit"
        logger.info(f"Prezentacja utworzona: {url}")
        return {"url": url, "presentation_id": pres_id, "title": pres_title}

    except Exception as e:
        logger.error(f"create_presentation error: {e}", exc_info=True)
        return {"error": str(e)}
