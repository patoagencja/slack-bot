"""PPTX presentation generator — Claude API generates content, python-pptx renders it."""
import io
import json
import logging
import os
import re
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

logger = logging.getLogger(__name__)

# ── Brand palette ──────────────────────────────────────────────────────────────
C_PURPLE = RGBColor(123, 97, 255)
C_PINK   = RGBColor(255, 77, 139)
C_ORANGE = RGBColor(255, 107, 53)
C_TEAL   = RGBColor(0, 191, 165)
C_NAVY   = RGBColor(18, 18, 48)
C_WHITE  = RGBColor(255, 255, 255)
C_LIGHT  = RGBColor(240, 238, 255)
C_GRAY   = RGBColor(100, 100, 120)
C_DARK   = RGBColor(30, 30, 60)

# Slide size: 33.87 x 19.05 cm  (16:9 widescreen)
SLIDE_W = Inches(13.33)
SLIDE_H = Inches(7.5)

KPI_COLORS = [C_PURPLE, C_PINK, C_ORANGE, C_TEAL, RGBColor(90, 180, 255), RGBColor(200, 100, 255)]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _bg(slide, color: RGBColor):
    """Fill slide background with a solid color."""
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def _box(slide, x, y, w, h, text="", font_size=18, bold=False,
         color=C_WHITE, align=PP_ALIGN.LEFT, bg=None, alpha=None):
    """Add a text box. Returns the shape."""
    txBox = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.color.rgb = color
    if bg:
        fill = txBox.fill
        fill.solid()
        fill.fore_color.rgb = bg
    return txBox


def _rect(slide, x, y, w, h, color: RGBColor, radius=False):
    """Add a filled rectangle."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE_TYPE.RECTANGLE
        Inches(x), Inches(y), Inches(w), Inches(h)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()  # no border
    return shape


def _add_slide(prs):
    """Add a blank slide."""
    blank_layout = prs.slide_layouts[6]
    return prs.slides.add_slide(blank_layout)


def _multi_para(tf, lines, font_size=14, color=C_WHITE, bold=False, spacing_after=6):
    """Fill a text frame with multiple paragraphs."""
    from pptx.util import Pt as _Pt
    from pptx.oxml.ns import qn
    from lxml import etree
    tf.clear()
    for i, line in enumerate(lines):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        run = p.add_run()
        run.text = line
        run.font.size = _Pt(font_size)
        run.font.color.rgb = color
        run.font.bold = bold
        # spacing after
        pPr = p._pPr
        if pPr is None:
            pPr = p._p.get_or_add_pPr()
        spcAft = etree.SubElement(pPr, qn('a:spcAft'))
        spcPts = etree.SubElement(spcAft, qn('a:spcPts'))
        spcPts.set('val', str(spacing_after * 100))


# ── Slide builders ─────────────────────────────────────────────────────────────

def _slide_title(prs, title, client_name="", subtitle="", date_range="", agency="Pato Agency"):
    slide = _add_slide(prs)
    _bg(slide, C_NAVY)

    # Accent bar (3 segments, top)
    segment_w = 13.33 / 3
    for i, col in enumerate([C_PURPLE, C_PINK, C_ORANGE]):
        _rect(slide, i * segment_w, 0, segment_w, 0.12, col)

    # Decorative circles (right side)
    for size, alpha_color in [(2.5, RGBColor(123, 97, 255)), (1.5, RGBColor(255, 77, 139))]:
        shape = slide.shapes.add_shape(9, Inches(10.8), Inches(2.5 - size/2), Inches(size), Inches(size))
        shape.fill.solid()
        shape.fill.fore_color.rgb = alpha_color
        shape.line.fill.background()

    # Agency label
    _box(slide, 0.5, 0.3, 5, 0.5, agency.upper(),
         font_size=9, color=C_PURPLE, bold=True)

    # Client badge
    if client_name:
        badge = slide.shapes.add_shape(1, Inches(0.5), Inches(1.0), Inches(2.2), Inches(0.42))
        badge.fill.solid()
        badge.fill.fore_color.rgb = C_PURPLE
        badge.line.fill.background()
        tf = badge.text_frame
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        run = tf.paragraphs[0].add_run()
        run.text = client_name.upper()
        run.font.size = Pt(11)
        run.font.bold = True
        run.font.color.rgb = C_WHITE

    # Main title
    _box(slide, 0.5, 1.65, 9.5, 2.2, title,
         font_size=38, bold=True, color=C_WHITE)

    # Subtitle
    if subtitle:
        _box(slide, 0.5, 3.85, 9.5, 0.7, subtitle,
             font_size=18, color=C_LIGHT)

    # Date range (bottom left)
    if date_range:
        _box(slide, 0.5, 6.5, 5, 0.5, f"📅  {date_range}",
             font_size=12, color=C_GRAY)

    # Bottom accent bar
    _rect(slide, 0, 7.38, 13.33, 0.12, C_PURPLE)


def _slide_kpis(prs, title, kpis):
    """KPI card slide. kpis = list of {"label", "value", "sub"?, "icon"?}"""
    slide = _add_slide(prs)
    _bg(slide, C_WHITE)

    # Header bar
    _rect(slide, 0, 0, 13.33, 1.1, C_NAVY)
    _box(slide, 0.5, 0.18, 12, 0.8, title,
         font_size=24, bold=True, color=C_WHITE)

    # Accent strip bottom of header
    _rect(slide, 0, 1.1, 4.44, 0.07, C_PURPLE)
    _rect(slide, 4.44, 1.1, 4.44, 0.07, C_PINK)
    _rect(slide, 8.88, 1.1, 4.45, 0.07, C_ORANGE)

    cols = min(len(kpis), 3)
    rows = (len(kpis) + cols - 1) // cols
    card_w = 12.0 / cols
    card_h = (5.8 / rows) - 0.15
    start_x = 0.67
    start_y = 1.4

    for i, kpi in enumerate(kpis):
        row = i // cols
        col = i % cols
        cx = start_x + col * (card_w + 0.18)
        cy = start_y + row * (card_h + 0.18)

        color = KPI_COLORS[i % len(KPI_COLORS)]

        # Card bg (very light tint)
        card = slide.shapes.add_shape(1, Inches(cx), Inches(cy), Inches(card_w), Inches(card_h))
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(245, 244, 255)
        card.line.color.rgb = RGBColor(220, 216, 255)
        card.line.width = Pt(1)

        # Top color strip
        _rect(slide, cx, cy, card_w, 0.08, color)

        # Icon + label
        icon = kpi.get("icon", "📊")
        label = kpi.get("label", "")
        _box(slide, cx + 0.15, cy + 0.18, card_w - 0.3, 0.4,
             f"{icon}  {label}", font_size=11, color=C_GRAY, bold=False)

        # Big value
        _box(slide, cx + 0.1, cy + 0.52, card_w - 0.2, card_h - 0.85,
             kpi.get("value", "—"),
             font_size=28, bold=True, color=C_DARK)

        # Sub-label
        if kpi.get("sub"):
            _box(slide, cx + 0.15, cy + card_h - 0.45, card_w - 0.3, 0.4,
                 kpi["sub"], font_size=10, color=C_GRAY)


def _slide_content(prs, title, bullets, header_color=C_NAVY):
    slide = _add_slide(prs)
    _bg(slide, C_WHITE)

    _rect(slide, 0, 0, 13.33, 1.1, header_color)
    _box(slide, 0.5, 0.18, 12, 0.8, title,
         font_size=24, bold=True, color=C_WHITE)
    _rect(slide, 0, 1.1, 0.07, 6.4, C_PURPLE)

    body_box = slide.shapes.add_textbox(Inches(0.4), Inches(1.35), Inches(12.5), Inches(5.8))
    tf = body_box.text_frame
    tf.word_wrap = True

    clean = [b for b in bullets if b.strip()]
    _multi_para(tf, clean, font_size=15, color=C_DARK, spacing_after=8)


def _slide_dark_insights(prs, title, bullets):
    slide = _add_slide(prs)
    _bg(slide, C_NAVY)

    _rect(slide, 0, 0, 0.07, 7.5, C_PURPLE)
    _box(slide, 0.35, 0.3, 12, 0.8, title,
         font_size=28, bold=True, color=C_WHITE)
    _rect(slide, 0.35, 1.15, 3, 0.05, C_PINK)

    body_box = slide.shapes.add_textbox(Inches(0.35), Inches(1.4), Inches(12.5), Inches(5.8))
    tf = body_box.text_frame
    tf.word_wrap = True
    clean = [b for b in bullets if b.strip()]
    _multi_para(tf, clean, font_size=15, color=C_LIGHT, spacing_after=10)


def _slide_table(prs, title, headers, rows):
    from pptx.util import Inches as _I, Pt as _Pt
    slide = _add_slide(prs)
    _bg(slide, C_WHITE)

    _rect(slide, 0, 0, 13.33, 1.1, C_NAVY)
    _box(slide, 0.5, 0.18, 12, 0.8, title,
         font_size=24, bold=True, color=C_WHITE)

    if not rows:
        return

    n_cols = len(headers)
    n_rows = len(rows)
    col_w = 12.0 / n_cols
    row_h = min(0.45, 5.5 / (n_rows + 1))
    table = slide.shapes.add_table(
        n_rows + 1, n_cols,
        _I(0.67), _I(1.3),
        _I(12.0), _I(row_h * (n_rows + 1))
    ).table

    # Header row
    for ci, h in enumerate(headers):
        cell = table.cell(0, ci)
        cell.fill.solid()
        cell.fill.fore_color.rgb = C_PURPLE
        tf = cell.text_frame
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        run = tf.paragraphs[0].add_run()
        run.text = str(h)
        run.font.size = _Pt(11)
        run.font.bold = True
        run.font.color.rgb = C_WHITE

    # Data rows
    for ri, row in enumerate(rows):
        bg = RGBColor(245, 244, 255) if ri % 2 == 0 else C_WHITE
        for ci, val in enumerate(row[:n_cols]):
            cell = table.cell(ri + 1, ci)
            cell.fill.solid()
            cell.fill.fore_color.rgb = bg
            tf = cell.text_frame
            tf.paragraphs[0].alignment = PP_ALIGN.CENTER
            run = tf.paragraphs[0].add_run()
            run.text = str(val)
            run.font.size = _Pt(10)
            run.font.color.rgb = C_DARK


# ── Content generation via Claude API ─────────────────────────────────────────

def _generate_slide_structure(title, client_name, subtitle, brief,
                               date_range, meta_ads_data, google_ads_data, extra_slides):
    """Call Claude API to generate structured slide content as JSON."""
    from anthropic import Anthropic
    ai = Anthropic(api_key=os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))

    data_ctx = ""
    if meta_ads_data:
        data_ctx += f"\n\nDANE META ADS:\n{json.dumps(meta_ads_data, ensure_ascii=False, indent=2)}"
    if google_ads_data:
        data_ctx += f"\n\nDANE GOOGLE ADS:\n{json.dumps(google_ads_data, ensure_ascii=False, indent=2)}"
    if brief:
        data_ctx += f"\n\nBRIEF:\n{brief}"
    if extra_slides:
        data_ctx += f"\n\nDODATKOWE SLAJDY ZASUGEROWANE:\n{json.dumps(extra_slides, ensure_ascii=False)}"

    prompt = f"""Jesteś ekspertem od prezentacji agencji marketingowej. Masz dane kampanii i musisz wygenerować strukturę prezentacji post-buy (raport wyników dla klienta) jako JSON.

TYTUŁ: {title}
KLIENT: {client_name or ""}
PODTYTUŁ: {subtitle or ""}
DATY: {date_range or ""}
{data_ctx}

Wygeneruj TYLKO JSON (bez markdown, bez ```), struktura:
{{
  "slides": [
    {{
      "type": "title",
      "title": "...",
      "subtitle": "...",
      "client": "...",
      "date_range": "..."
    }},
    {{
      "type": "kpis",
      "title": "Podsumowanie KPI",
      "kpis": [
        {{"label": "Zasięg", "value": "45 232", "icon": "👥", "sub": "unikalnych użytkowników"}},
        {{"label": "Wyświetlenia", "value": "89 450", "icon": "👁️", "sub": "łączne impressions"}},
        {{"label": "Wydatek", "value": "1 234,56 zł", "icon": "💰", "sub": "całkowity budżet"}},
        {{"label": "Kliknięcia", "value": "1 023", "icon": "🖱️", "sub": "link clicks"}},
        {{"label": "CPM", "value": "13,80 zł", "icon": "📊", "sub": "koszt za 1000 wyświetleń"}},
        {{"label": "CPC", "value": "1,21 zł", "icon": "💡", "sub": "koszt za kliknięcie"}}
      ]
    }},
    {{
      "type": "content",
      "title": "Wyniki kampanii",
      "bullets": ["• Kampania X: ...", "• CTR: 2.3%", "..."],
      "dark": false
    }},
    {{
      "type": "table",
      "title": "Wyniki według kampanii",
      "headers": ["Kampania", "Wydatek", "Zasięg", "CTR"],
      "rows": [["Kampania A", "500 zł", "10 000", "2.1%"]]
    }},
    {{
      "type": "content",
      "title": "Wnioski i rekomendacje",
      "bullets": ["✅ ...", "⚠️ ...", "🚀 ..."],
      "dark": true
    }}
  ]
}}

ZASADY:
- Użyj PRAWDZIWYCH liczb z danych (nie szacunków)
- Przelicz zł z USD jeśli spend jest w dolarach (kurs 4.0)
- Polskie opisy, profesjonalne, konkretne
- Slajd wnioski jako ciemny (dark: true)
- Jeśli są dane breakdown (placement, device) → dodaj slajd table z tymi danymi
- Min 4 slajdy, max 8"""

    resp = ai.messages.create(
        model="claude-opus-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()
    return json.loads(raw)


# ── Main export function ───────────────────────────────────────────────────────

def generate_pptx(title, client_name=None, subtitle=None, brief=None,
                  date_range=None, meta_ads_data=None, google_ads_data=None,
                  extra_slides=None):
    """
    Generate a PPTX presentation and return it as bytes.
    Raises on error.
    """
    try:
        from pptx import Presentation as _Prs  # noqa: verify import
    except ImportError:
        raise RuntimeError("python-pptx nie zainstalowany — uruchom: pip install python-pptx")

    structure = _generate_slide_structure(
        title, client_name, subtitle, brief,
        date_range, meta_ads_data, google_ads_data, extra_slides
    )

    prs = Presentation()
    prs.slide_width  = SLIDE_W
    prs.slide_height = SLIDE_H

    for slide_def in structure.get("slides", []):
        stype = slide_def.get("type", "content")

        if stype == "title":
            _slide_title(
                prs,
                title=slide_def.get("title", title),
                client_name=slide_def.get("client", client_name or ""),
                subtitle=slide_def.get("subtitle", subtitle or ""),
                date_range=slide_def.get("date_range", date_range or ""),
            )
        elif stype == "kpis":
            _slide_kpis(prs, slide_def.get("title", "KPI"), slide_def.get("kpis", []))
        elif stype == "table":
            _slide_table(
                prs,
                slide_def.get("title", ""),
                slide_def.get("headers", []),
                slide_def.get("rows", []),
            )
        elif stype == "content":
            bullets = slide_def.get("bullets", [])
            dark = slide_def.get("dark", False)
            t = slide_def.get("title", "")
            if dark:
                _slide_dark_insights(prs, t, bullets)
            else:
                _slide_content(prs, t, bullets)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()


def share_pptx(pptx_bytes: bytes, filename: str) -> str | None:
    """
    Upload PPTX bytes to a temporary file host and return a download URL.
    Tries transfer.sh, then 0x0.st as fallback.
    Returns URL string or None on failure.
    """
    import requests as _req
    safe_name = filename.replace(" ", "_")

    # Attempt 1: transfer.sh (14-day links)
    try:
        r = _req.put(
            f"https://transfer.sh/{safe_name}",
            data=pptx_bytes,
            headers={"Max-Days": "14"},
            timeout=30,
        )
        if r.ok and r.text.strip().startswith("http"):
            return r.text.strip()
    except Exception as _e:
        logger.warning(f"transfer.sh failed: {_e}")

    # Attempt 2: 0x0.st (permanent until unused)
    try:
        r = _req.post(
            "https://0x0.st",
            files={"file": (safe_name, pptx_bytes, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
            timeout=30,
        )
        if r.ok and r.text.strip().startswith("http"):
            return r.text.strip()
    except Exception as _e:
        logger.warning(f"0x0.st failed: {_e}")

    return None


# ══════════════════════════════════════════════════════════════════════════════
# DRE — BRANDED REPORT GENERATOR (premium dark theme, no Claude API call)
# ══════════════════════════════════════════════════════════════════════════════

DRE_DARK  = RGBColor(0x1A, 0x1A, 0x1A)
DRE_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
DRE_GOLD  = RGBColor(0xE8, 0xA0, 0x50)
DRE_CARD  = RGBColor(0x2A, 0x2A, 0x2A)
DRE_GREEN = RGBColor(0x2E, 0xCC, 0x71)
DRE_RED   = RGBColor(0xE7, 0x4C, 0x3C)
DRE_GRAY  = RGBColor(0xAA, 0xAA, 0xAA)
DRE_SHADOW = RGBColor(0x0A, 0x0A, 0x0A)
DRE_CARD_BORDER = RGBColor(0x3A, 0x3A, 0x3A)


def _dre_bg(slide, color: RGBColor):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def _dre_box(slide, x, y, w, h, text="", font_size=14, bold=False,
             color=None, align=PP_ALIGN.LEFT, font_name="Calibri"):
    if color is None:
        color = DRE_WHITE
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font_name
    return tb


def _dre_rect(slide, x, y, w, h, color: RGBColor, border_color=None):
    shape = slide.shapes.add_shape(1, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    if border_color:
        shape.line.color.rgb = border_color
        shape.line.width = Pt(0.5)
    else:
        shape.line.fill.background()
    return shape


def _dre_stat_card(slide, x, y, w, h, value, label):
    # Shadow
    shadow = slide.shapes.add_shape(1, Inches(x + 0.04), Inches(y + 0.04), Inches(w), Inches(h))
    shadow.fill.solid()
    shadow.fill.fore_color.rgb = DRE_SHADOW
    shadow.line.fill.background()
    # Card
    card = slide.shapes.add_shape(1, Inches(x), Inches(y), Inches(w), Inches(h))
    card.fill.solid()
    card.fill.fore_color.rgb = DRE_CARD
    card.line.color.rgb = DRE_CARD_BORDER
    card.line.width = Pt(0.5)
    # Value
    vb = slide.shapes.add_textbox(Inches(x), Inches(y + 0.1), Inches(w), Inches(h * 0.58))
    vtf = vb.text_frame
    vtf.word_wrap = False
    vp = vtf.paragraphs[0]
    vp.alignment = PP_ALIGN.CENTER
    vr = vp.add_run()
    vr.text = value
    vr.font.size = Pt(28)
    vr.font.bold = True
    vr.font.color.rgb = DRE_GOLD
    vr.font.name = "Cambria"
    # Label
    lb = slide.shapes.add_textbox(Inches(x), Inches(y + h * 0.62), Inches(w), Inches(h * 0.35))
    ltf = lb.text_frame
    ltf.word_wrap = True
    lp = ltf.paragraphs[0]
    lp.alignment = PP_ALIGN.CENTER
    lr = lp.add_run()
    lr.text = label
    lr.font.size = Pt(11)
    lr.font.color.rgb = DRE_GRAY
    lr.font.name = "Calibri"


# ── DRE Data Analysis ──────────────────────────────────────────────────────────

def _dre_normalize_campaign(c, platform="meta"):
    """Normalize Meta or Google campaign dict to a common schema."""
    if platform == "google":
        name = (c.get("campaign_name") or c.get("campaign.name") or
                c.get("name") or "Nieznana kampania")
        cost = c.get("spend") or c.get("cost") or c.get("metrics.cost_micros") or 0
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            cost = 0.0
        if cost > 100000:  # micros
            cost /= 1_000_000
        impressions = int(c.get("impressions") or c.get("metrics.impressions") or 0)
        clicks = int(c.get("clicks") or c.get("metrics.clicks") or 0)
        ctr = float(c.get("ctr") or c.get("metrics.ctr") or 0)
        # Google API returns fraction 0-1; normalize tool might already be %
        if 0 < ctr <= 1.0:
            ctr *= 100
        cpc = float(c.get("average_cpc") or c.get("cpc") or c.get("metrics.average_cpc") or 0)
        if cpc > 1000:
            cpc /= 1_000_000
        conversions = float(c.get("conversions") or c.get("metrics.conversions") or 0)
        return {"name": name, "spend": cost, "impressions": impressions,
                "clicks": clicks, "ctr": ctr, "cpc": cpc, "conversions": conversions,
                "reach": 0, "platform": "Google"}
    else:
        name = c.get("campaign_name") or c.get("name") or "Nieznana kampania"
        return {
            "name": name,
            "spend": float(c.get("spend", 0) or 0),
            "impressions": int(c.get("impressions", 0) or 0),
            "clicks": int(c.get("clicks", 0) or 0),
            "ctr": float(c.get("ctr", 0) or 0),
            "cpc": float(c.get("cpc", 0) or 0),
            "conversions": float(c.get("conversions", 0) or 0),
            "reach": int(c.get("reach", 0) or 0),
            "platform": "Meta",
        }


def _dre_to_list(d):
    if not d:
        return []
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        return d.get("data", [])
    return []


def _dre_analyze(meta_ads_data, google_ads_data):
    """Aggregate campaign data and produce KPIs, alerts, top3, recommendations."""
    meta_raw = [_dre_normalize_campaign(c, "meta") for c in _dre_to_list(meta_ads_data)]
    google_raw = [_dre_normalize_campaign(c, "google") for c in _dre_to_list(google_ads_data)]
    all_camps = meta_raw + google_raw

    meta_spend = sum(c["spend"] for c in meta_raw)
    google_spend = sum(c["spend"] for c in google_raw)
    total_spend = meta_spend + google_spend
    total_reach = sum(c["reach"] for c in meta_raw)
    total_impressions = sum(c["impressions"] for c in all_camps)
    total_clicks = sum(c["clicks"] for c in all_camps)
    cpc = total_spend / total_clicks if total_clicks > 0 else 0.0
    cpm = total_spend / total_impressions * 1000 if total_impressions > 0 else 0.0

    # Alerts
    alerts = []
    seen_alerts = set()

    def _add_alert(level, text):
        key = text[:60]
        if key not in seen_alerts:
            seen_alerts.add(key)
            alerts.append({"level": level, "text": text})

    for c in google_raw:
        if c["clicks"] > 500 and c["conversions"] == 0:
            _add_alert("critical",
                f"KRYTYCZNY: problem z trackingiem — {c['name']} "
                f"({c['clicks']} klikniec, 0 konwersji)")
        if c["ctr"] > 0 and c["ctr"] < 0.10 and c["impressions"] > 100:
            _add_alert("stop", f"STOP: {c['name']} — nieefektywna (CTR {c['ctr']:.2f}%)")
        if 0 < c["cpc"] > 3.0:
            _add_alert("review",
                f"REVIEW: wysoki koszt kliknięcia — {c['name']} ({c['cpc']:.2f} zł/klik)")

    for c in meta_raw:
        if c["ctr"] > 0 and c["ctr"] < 0.10 and c["impressions"] > 100:
            _add_alert("stop", f"STOP: {c['name']} — nieefektywna (CTR {c['ctr']:.2f}%)")
        if 0 < c["cpc"] > 3.0:
            _add_alert("review",
                f"REVIEW: wysoki koszt kliknięcia — {c['name']} ({c['cpc']:.2f} zł/klik)")

    # Top 3 by CTR (include only campaigns with impressions > 0)
    ranked = sorted([c for c in all_camps if c["impressions"] > 0],
                    key=lambda x: x["ctr"], reverse=True)
    top3 = ranked[:3]

    # Recommendations (max 4, prioritized)
    recs = []
    # FIX: critical tracking first
    if any(a["level"] == "critical" for a in alerts):
        recs.append({"action": "FIX",
                     "text": "Napraw tracking konwersji Google Ads — brak danych o wynikach kampanii"})
    # STOP: worst CTR
    worst = sorted([c for c in all_camps if c["ctr"] > 0 and c["ctr"] < 0.10
                    and c["impressions"] > 200], key=lambda x: x["ctr"])
    if worst:
        w = worst[0]
        recs.append({"action": "STOP",
                     "text": f"{w['name']} — CTR {w['ctr']:.2f}%, brak efektywnosci. Wstrzymaj kampanię."})
    # SCALE: best CTR
    if top3 and top3[0]["ctr"] >= 1.0:
        b = top3[0]
        recs.append({"action": "SCALE",
                     "text": f"{b['name']} — CTR {b['ctr']:.2f}%. Zwiększ budżet o 20-30%."})
    # OPTYMALIZUJ: budget split
    if google_spend > 0 and meta_spend > 0 and total_spend > 0:
        if google_spend / total_spend > 0.6:
            shift = google_spend * 0.10
            recs.append({"action": "OPTYMALIZUJ",
                         "text": f"Przesuń ~{shift:.0f} zł z Google na Meta — niższy CPM, szerszy zasięg."})
        elif meta_spend / total_spend > 0.8:
            recs.append({"action": "OPTYMALIZUJ",
                         "text": "Przetestuj Google Search dla kampanii brandowych DRE."})
    # Fallback recommendation
    while len(recs) < 2:
        recs.append({"action": "OPTYMALIZUJ",
                     "text": "Monitoruj frequency kampanii Meta — nie przekraczaj 4.0 w tygodniu."})

    return {
        "total_reach": total_reach,
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "total_spend": total_spend,
        "meta_spend": meta_spend,
        "google_spend": google_spend,
        "cpc": cpc,
        "cpm": cpm,
        "top3": top3,
        "alerts": alerts,
        "recommendations": recs[:4],
    }


# ── DRE Slide Builders ─────────────────────────────────────────────────────────

def _dre_gold_rule(slide, x, y, w=3.0):
    _dre_rect(slide, x, y, w, 0.04, DRE_GOLD)


def _dre_slide_title(prs, title, date_range=""):
    slide = _add_slide(prs)
    _dre_bg(slide, DRE_DARK)

    # DRE logo label
    _dre_box(slide, 0.5, 0.45, 3, 1.1, "DRE",
             font_size=52, bold=True, color=DRE_WHITE, font_name="Cambria")

    # Main title
    _dre_box(slide, 0.5, 1.75, 10, 1.9, title,
             font_size=36, bold=True, color=DRE_WHITE, font_name="Cambria")

    # Date range (gold)
    if date_range:
        _dre_box(slide, 0.5, 3.75, 9, 0.75, date_range,
                 font_size=20, color=DRE_GOLD, font_name="Calibri")

    # Gold accent line
    _dre_gold_rule(slide, 0.5, 5.1, 4.0)

    # PATO AGENCY corner label
    _dre_box(slide, 0.5, 6.95, 4, 0.4, "PATO AGENCY",
             font_size=9, bold=True, color=DRE_GOLD, font_name="Calibri")


def _dre_slide_kpis(prs, d):
    slide = _add_slide(prs)
    _dre_bg(slide, DRE_DARK)

    _dre_box(slide, 0.5, 0.22, 12, 0.75, "Podsumowanie KPI",
             font_size=24, bold=True, color=DRE_WHITE, font_name="Cambria")
    _dre_gold_rule(slide, 0.5, 0.97)

    def _fmt_num(n):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(int(n))

    def _fmt_pln(v):
        s = f"{v:,.0f}".replace(",", " ")
        return f"{s} zł"

    kpis = [
        (_fmt_num(d["total_reach"]) if d["total_reach"] > 0 else "N/A", "Zasięg"),
        (_fmt_num(d["total_impressions"]) if d["total_impressions"] > 0 else "N/A", "Wyświetlenia"),
        (_fmt_num(d["total_clicks"]) if d["total_clicks"] > 0 else "N/A", "Kliknięcia"),
        (_fmt_pln(d["total_spend"]) if d["total_spend"] > 0 else "N/A", "Wydatek"),
        (f"{d['cpc']:.2f} zł" if d["cpc"] > 0 else "N/A", "CPC"),
        (f"{d['cpm']:.2f} zł" if d["cpm"] > 0 else "N/A", "CPM"),
    ]

    card_w, card_h = 3.9, 2.85
    gap_x, gap_y  = 0.265, 0.4
    sx, sy        = 0.5, 1.2

    for i, (value, label) in enumerate(kpis):
        row, col = divmod(i, 3)
        _dre_stat_card(slide,
                       sx + col * (card_w + gap_x),
                       sy + row * (card_h + gap_y),
                       card_w, card_h, value, label)


def _dre_slide_budget(prs, d, date_range=""):
    slide = _add_slide(prs)
    _dre_bg(slide, DRE_WHITE)

    # Header bar (dark)
    _dre_rect(slide, 0, 0, 13.33, 1.1, DRE_DARK)
    _dre_box(slide, 0.5, 0.18, 12, 0.75, "Budżet & Kanały",
             font_size=24, bold=True, color=DRE_WHITE, font_name="Cambria")

    total = d["total_spend"]
    meta_s = d["meta_spend"]
    google_s = d["google_spend"]
    meta_pct = meta_s / total * 100 if total > 0 else 0
    google_pct = google_s / total * 100 if total > 0 else 0

    bar_max_w = 5.5
    bar_x_start = 2.3
    bar_h = 0.75

    # Meta row
    _dre_box(slide, 0.6, 1.7, 1.6, 0.45, "META ADS",
             font_size=11, bold=True, color=DRE_DARK, font_name="Calibri")
    meta_bar_w = max(0.08, bar_max_w * meta_pct / 100)
    _dre_rect(slide, bar_x_start, 1.55, meta_bar_w, bar_h, DRE_GOLD)
    _dre_box(slide, bar_x_start + meta_bar_w + 0.2, 1.55, 2.5, bar_h,
             f"{meta_pct:.0f}%  {meta_s:,.0f} zł".replace(",", " "),
             font_size=13, bold=True, color=DRE_DARK, font_name="Calibri")

    # Google row
    _dre_box(slide, 0.6, 3.0, 1.6, 0.45, "GOOGLE ADS",
             font_size=11, bold=True, color=DRE_DARK, font_name="Calibri")
    google_bar_w = max(0.08, bar_max_w * google_pct / 100)
    _dre_rect(slide, bar_x_start, 2.85, google_bar_w, bar_h, RGBColor(0x33, 0x33, 0x33))
    _dre_box(slide, bar_x_start + google_bar_w + 0.2, 2.85, 2.5, bar_h,
             f"{google_pct:.0f}%  {google_s:,.0f} zł".replace(",", " "),
             font_size=13, bold=True, color=DRE_DARK, font_name="Calibri")

    # Separator
    _dre_rect(slide, 9.3, 1.2, 0.03, 5.8, RGBColor(0xDD, 0xDD, 0xDD))

    # Summary column (right)
    sx = 9.6
    _dre_box(slide, sx, 1.3, 3.5, 0.35, "TOTAL SPEND",
             font_size=10, color=RGBColor(0x88, 0x88, 0x88), font_name="Calibri")
    _dre_box(slide, sx, 1.65, 3.5, 0.65,
             f"{total:,.0f} zł".replace(",", " ") if total > 0 else "N/A",
             font_size=22, bold=True, color=DRE_DARK, font_name="Cambria")

    _dre_rect(slide, sx, 2.45, 3.1, 0.03, RGBColor(0xDD, 0xDD, 0xDD))

    _dre_box(slide, sx, 2.6, 3.5, 0.32, "Meta Ads",
             font_size=10, color=RGBColor(0x88, 0x88, 0x88), font_name="Calibri")
    _dre_box(slide, sx, 2.92, 3.5, 0.4,
             f"{meta_pct:.0f}% — {meta_s:,.0f} zł".replace(",", " "),
             font_size=13, bold=True, color=DRE_DARK, font_name="Calibri")

    _dre_box(slide, sx, 3.45, 3.5, 0.32, "Google Ads",
             font_size=10, color=RGBColor(0x88, 0x88, 0x88), font_name="Calibri")
    _dre_box(slide, sx, 3.77, 3.5, 0.4,
             f"{google_pct:.0f}% — {google_s:,.0f} zł".replace(",", " "),
             font_size=13, bold=True, color=DRE_DARK, font_name="Calibri")

    _dre_rect(slide, sx, 4.3, 3.1, 0.03, RGBColor(0xDD, 0xDD, 0xDD))

    # Monthly projection
    days = 7
    if date_range:
        import re as _re
        parts = _re.findall(r'(\d{2})\.(\d{2})(?:\.(\d{4}))?', date_range)
        if len(parts) >= 2:
            try:
                from datetime import datetime as _dt
                year = parts[-1][2] or "2026"
                d1 = _dt.strptime(f"{parts[0][0]}.{parts[0][1]}.{year}", "%d.%m.%Y")
                d2 = _dt.strptime(f"{parts[-1][0]}.{parts[-1][1]}.{year}", "%d.%m.%Y")
                days = max(1, abs((d2 - d1).days) + 1)
            except Exception:
                days = 7
    monthly_proj = total / days * 30 if days > 0 and total > 0 else 0

    _dre_box(slide, sx, 4.45, 3.5, 0.32, "Projekcja miesięczna",
             font_size=10, color=RGBColor(0x88, 0x88, 0x88), font_name="Calibri")
    _dre_box(slide, sx, 4.77, 3.5, 0.55,
             f"~{monthly_proj:,.0f} zł".replace(",", " ") if monthly_proj > 0 else "N/A",
             font_size=18, bold=True, color=DRE_GOLD, font_name="Cambria")


def _dre_slide_top_performers(prs, d):
    slide = _add_slide(prs)
    _dre_bg(slide, DRE_DARK)

    _dre_box(slide, 0.5, 0.22, 12, 0.75, "Top Performers",
             font_size=24, bold=True, color=DRE_WHITE, font_name="Cambria")
    _dre_gold_rule(slide, 0.5, 0.97)

    top3 = d.get("top3", [])

    if not top3:
        _dre_box(slide, 0.5, 2.5, 12, 0.8, "Brak danych o kampaniach.",
                 font_size=16, color=DRE_GRAY)
        return

    def _ctr_color(ctr, platform):
        if platform == "Meta":
            return DRE_GREEN if ctr >= 1.0 else (DRE_RED if ctr < 0.5 else DRE_WHITE)
        return DRE_GREEN if ctr >= 10.0 else (DRE_RED if ctr < 0.5 else DRE_WHITE)

    def _cpc_color(cpc):
        return DRE_GREEN if cpc <= 0.5 else (DRE_RED if cpc > 2.0 else DRE_WHITE)

    card_ys = [1.25, 3.05, 4.85]
    card_h  = 1.6

    for i, camp in enumerate(top3):
        cy = card_ys[i]
        # Shadow + card
        shadow = slide.shapes.add_shape(1, Inches(0.54), Inches(cy + 0.04),
                                        Inches(12.25), Inches(card_h))
        shadow.fill.solid()
        shadow.fill.fore_color.rgb = DRE_SHADOW
        shadow.line.fill.background()

        card = slide.shapes.add_shape(1, Inches(0.5), Inches(cy),
                                      Inches(12.3), Inches(card_h))
        card.fill.solid()
        card.fill.fore_color.rgb = DRE_CARD
        card.line.color.rgb = DRE_CARD_BORDER
        card.line.width = Pt(0.5)

        # Gold left accent for rank #1
        acc = slide.shapes.add_shape(1, Inches(0.5), Inches(cy),
                                     Inches(0.08), Inches(card_h))
        acc.fill.solid()
        acc.fill.fore_color.rgb = DRE_GOLD if i == 0 else RGBColor(0x44, 0x44, 0x44)
        acc.line.fill.background()

        # Campaign name + platform
        _dre_box(slide, 0.75, cy + 0.18, 6.5, 0.55, camp["name"],
                 font_size=14, bold=True, color=DRE_WHITE, font_name="Cambria")
        _dre_box(slide, 0.75, cy + 0.75, 2, 0.4, camp["platform"],
                 font_size=10, color=DRE_GRAY, font_name="Calibri")

        # Metrics
        ctr_c = _ctr_color(camp["ctr"], camp["platform"])
        _dre_box(slide, 7.6, cy + 0.18, 1.8, 0.35, "CTR",
                 font_size=10, color=DRE_GRAY, font_name="Calibri")
        _dre_box(slide, 7.6, cy + 0.55, 1.8, 0.65, f"{camp['ctr']:.2f}%",
                 font_size=20, bold=True, color=ctr_c, font_name="Cambria")

        cpc_c = _cpc_color(camp["cpc"])
        _dre_box(slide, 9.6, cy + 0.18, 1.8, 0.35, "CPC",
                 font_size=10, color=DRE_GRAY, font_name="Calibri")
        _dre_box(slide, 9.6, cy + 0.55, 1.8, 0.65,
                 f"{camp['cpc']:.2f} zł" if camp["cpc"] > 0 else "N/A",
                 font_size=20, bold=True, color=cpc_c, font_name="Cambria")

        _dre_box(slide, 11.5, cy + 0.18, 1.3, 0.35, "SPEND",
                 font_size=10, color=DRE_GRAY, font_name="Calibri")
        _dre_box(slide, 11.5, cy + 0.55, 1.3, 0.65,
                 f"{camp['spend']:.0f} zł",
                 font_size=14, bold=True, color=DRE_WHITE, font_name="Cambria")

    # Key insight (gold, bottom)
    if top3:
        b = top3[0]
        _dre_box(slide, 0.5, 6.7, 12.3, 0.5,
                 f"Najlepszy wynik: {b['name']} — CTR {b['ctr']:.2f}%, CPC {b['cpc']:.2f} zł",
                 font_size=12, bold=True, color=DRE_GOLD, font_name="Calibri")


def _dre_slide_alerts(prs, d):
    slide = _add_slide(prs)
    _dre_bg(slide, DRE_WHITE)

    _dre_rect(slide, 0, 0, 13.33, 1.1, DRE_DARK)
    _dre_box(slide, 0.5, 0.18, 12, 0.75, "Problemy & Alerty",
             font_size=24, bold=True, color=DRE_WHITE, font_name="Cambria")

    alerts = d.get("alerts", [])

    if not alerts:
        ok = slide.shapes.add_shape(1, Inches(0.5), Inches(1.5), Inches(12.3), Inches(1.2))
        ok.fill.solid()
        ok.fill.fore_color.rgb = RGBColor(0xE8, 0xF8, 0xF0)
        ok.line.color.rgb = DRE_GREEN
        ok.line.width = Pt(1.5)
        _dre_box(slide, 0.75, 1.72, 11.8, 0.8,
                 "Brak krytycznych problemow — wszystkie kampanie w normie.",
                 font_size=16, color=RGBColor(0x1A, 0x7A, 0x45), font_name="Calibri")
        return

    y = 1.3
    level_styles = {
        "critical": (RGBColor(0xFD, 0xED, 0xED), DRE_RED, True),
        "stop":     (RGBColor(0xFF, 0xF0, 0xF0), DRE_RED, False),
        "review":   (RGBColor(0xFF, 0xF9, 0xED), RGBColor(0xE0, 0x90, 0x10), False),
    }

    for alert in alerts[:5]:
        level = alert["level"]
        bg, border, is_bold = level_styles.get(level, level_styles["review"])

        card = slide.shapes.add_shape(1, Inches(0.5), Inches(y), Inches(12.3), Inches(0.85))
        card.fill.solid()
        card.fill.fore_color.rgb = bg
        card.line.color.rgb = border
        card.line.width = Pt(1.5)

        ind = slide.shapes.add_shape(1, Inches(0.5), Inches(y), Inches(0.1), Inches(0.85))
        ind.fill.solid()
        ind.fill.fore_color.rgb = border
        ind.line.fill.background()

        _dre_box(slide, 0.75, y + 0.15, 11.8, 0.58, alert["text"],
                 font_size=13, bold=is_bold, color=DRE_DARK, font_name="Calibri")
        y += 1.0


def _dre_slide_recommendations(prs, d):
    slide = _add_slide(prs)
    _dre_bg(slide, DRE_DARK)

    _dre_box(slide, 0.5, 0.22, 12, 0.75, "Rekomendacje na nastepny tydzien",
             font_size=24, bold=True, color=DRE_WHITE, font_name="Cambria")
    _dre_gold_rule(slide, 0.5, 0.97)

    ACTION_COLORS = {
        "STOP":       DRE_RED,
        "SCALE":      DRE_GREEN,
        "FIX":        RGBColor(0xE0, 0x90, 0x10),
        "OPTYMALIZUJ": RGBColor(0x5B, 0xB5, 0xFF),
    }

    recs = d.get("recommendations", [])
    y = 1.25

    for i, rec in enumerate(recs[:4]):
        action = rec.get("action", "OPTYMALIZUJ")
        text   = rec.get("text", "")
        action_color = ACTION_COLORS.get(action, DRE_GOLD)
        is_top = (i == 0)

        card_bg = RGBColor(0x2A, 0x24, 0x18) if is_top else DRE_CARD
        card_border = DRE_GOLD if is_top else DRE_CARD_BORDER

        card = slide.shapes.add_shape(1, Inches(0.5), Inches(y), Inches(12.3), Inches(1.05))
        card.fill.solid()
        card.fill.fore_color.rgb = card_bg
        card.line.color.rgb = card_border
        card.line.width = Pt(1.5 if is_top else 0.5)

        # Action badge
        badge = slide.shapes.add_shape(1, Inches(0.65), Inches(y + 0.3),
                                       Inches(1.65), Inches(0.45))
        badge.fill.solid()
        badge.fill.fore_color.rgb = action_color
        badge.line.fill.background()
        btf = badge.text_frame
        bp  = btf.paragraphs[0]
        bp.alignment = PP_ALIGN.CENTER
        br = bp.add_run()
        br.text = action
        br.font.size = Pt(10)
        br.font.bold = True
        br.font.color.rgb = DRE_WHITE
        br.font.name = "Calibri"

        _dre_box(slide, 2.5, y + 0.25, 10.0, 0.6, text,
                 font_size=13, color=DRE_GOLD if is_top else DRE_WHITE,
                 font_name="Calibri")

        if is_top:
            _dre_box(slide, 0.65, y + 0.82, 6, 0.2, "MOST IMPORTANT ACTION",
                     font_size=8, color=DRE_GOLD, font_name="Calibri")

        y += 1.22


# ── DRE Main Entry Point ───────────────────────────────────────────────────────

def generate_pptx_dre(title=None, date_range=None,
                      meta_ads_data=None, google_ads_data=None):
    """
    Generate a DRE-branded PPTX report (6 slides, dark premium theme).
    Does NOT call Claude API — builds slides directly from ads data.
    Returns bytes.
    """
    try:
        from pptx import Presentation as _Prs  # noqa: verify import
    except ImportError:
        raise RuntimeError("python-pptx nie zainstalowany — uruchom: pip install python-pptx")

    if not title:
        title = "Wyniki kampanii DRE"

    d = _dre_analyze(meta_ads_data, google_ads_data)

    prs = Presentation()
    prs.slide_width  = SLIDE_W
    prs.slide_height = SLIDE_H

    _dre_slide_title(prs, title, date_range or "")
    _dre_slide_kpis(prs, d)
    _dre_slide_budget(prs, d, date_range or "")
    _dre_slide_top_performers(prs, d)
    _dre_slide_alerts(prs, d)
    _dre_slide_recommendations(prs, d)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()
