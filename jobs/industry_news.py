"""
Weekly industry news digest — poniedziałek 9:00.
Zbiera nowości z Meta Ads, Google Ads, TikTok Ads oraz AI tools,
kondensuje je przez Claude i wysyła na kanał #media.

Główna wiadomość: same punkty (nagłówki).
Wątek: rozwinięcie każdego punktu osobno.
"""
import os
import json
import re
import logging
from datetime import datetime, timedelta

import _ctx
from config.constants import PUBLISHED_NEWS_FILE

logger = logging.getLogger(__name__)

MEDIA_CHANNEL_ID = os.environ.get("MEDIA_CHANNEL_ID", "C0AKFBL2JR1")

NEWS_PROMPT = """Przeszukaj internet i znajdź najważniejsze nowości z ostatnich 7 dni w: Meta Ads, Google Ads, TikTok Ads, AI w reklamie.

Skup się na zmianach praktycznych dla performance marketingu: nowe funkcje, rollouty, testy, zmiany w targetowaniu, atrybucji, automatyzacji, kreacjach.

Zasady:
- tylko oficjalne źródła i renomowane media branżowe,
- nie duplikuj newsów,
- tylko potwierdzone informacje (testy/zapowiedzi oznacz jako "(test)" lub "(zapowiedź)"),
- maksymalnie 8 punktów.
{exclusion_block}
Zwróć odpowiedź jako JSON (bez żadnego tekstu przed ani po):
{{
  "points": [
    {{
      "platform": "Meta Ads",
      "headline": "Krótki nagłówek — maks. 80 znaków",
      "detail": "Pełne rozwinięcie: co się zmieniło, dlaczego ważne dla performance marketingu, jak wykorzystać w praktyce. 3-5 zdań.",
      "url": "https://..."
    }}
  ]
}}"""


# ── Published-news store ────────────────────────────────────────────────────────

def _load_published() -> list[dict]:
    """Zwraca listę {url, date} z ostatnich 30 dni."""
    if not os.path.exists(PUBLISHED_NEWS_FILE):
        return []
    try:
        with open(PUBLISHED_NEWS_FILE) as f:
            entries = json.load(f)
        cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        return [e for e in entries if e.get("date", "") >= cutoff]
    except Exception:
        return []


def _save_published(entries: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(PUBLISHED_NEWS_FILE), exist_ok=True)
        with open(PUBLISHED_NEWS_FILE, "w") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        logger.error(f"Błąd zapisu published_news: {e}")


def _record_urls(urls: list[str]) -> None:
    entries = _load_published()
    today = datetime.now().strftime('%Y-%m-%d')
    existing = {e["url"] for e in entries}
    for url in urls:
        if url and url not in existing:
            entries.append({"url": url, "date": today})
    _save_published(entries)


# ── Core logic ──────────────────────────────────────────────────────────────────

def _fetch_with_web_search(prompt: str) -> str:
    response = _ctx.claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 10,
        }],
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [block.text for block in response.content if hasattr(block, "text")]
    return "\n".join(parts).strip()


def _parse_points(raw: str) -> list[dict]:
    """Wyciąga listę punktów z odpowiedzi JSON Claude'a."""
    try:
        # Claude może owinąć JSON w ```json ... ```
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            data = json.loads(match.group())
            return data.get("points", [])
    except Exception as e:
        logger.warning(f"Nie udało się sparsować JSON z newsów: {e}")
    return []


PLATFORM_EMOJI = {
    "meta": "🔵", "google": "🟡", "tiktok": "⚫", "ai": "🤖",
}

def _platform_emoji(platform: str) -> str:
    p = platform.lower()
    for key, emoji in PLATFORM_EMOJI.items():
        if key in p:
            return emoji
    return "📌"


def generate_industry_news_digest() -> tuple[str, list[dict]]:
    """
    Zwraca (main_text, points) gdzie:
    - main_text: lista nagłówków do głównej wiadomości
    - points: lista słowników z rozwinięciami do wątku
    """
    now = datetime.now()
    logger.info("🔍 Szukam nowości branżowych...")

    published = _load_published()
    if published:
        url_list = "\n".join(f"• {e['url']}" for e in published)
        exclusion_block = (
            f"\nNIE UŻYWAJ następujących źródeł/artykułów — były już opublikowane:\n"
            f"{url_list}\n"
            f"Znajdź wyłącznie nowe artykuły, których nie ma na powyższej liście.\n"
        )
    else:
        exclusion_block = ""

    prompt = NEWS_PROMPT.format(exclusion_block=exclusion_block)

    try:
        raw = _fetch_with_web_search(prompt)
    except Exception as e:
        logger.error(f"Błąd pobierania nowości: {e}")
        fallback = f"❌ Nie udało się wygenerować digestu: {e}"
        return fallback, []

    points = _parse_points(raw)

    if not points:
        # Fallback: zwróć surową odpowiedź jako jedną wiadomość
        logger.warning("Brak punktów w odpowiedzi — fallback do surowego tekstu")
        return raw + f"\n\n_Wygenerowano przez Sebol • {now.strftime('%d.%m.%Y %H:%M')}_", []

    _record_urls([p.get("url", "") for p in points])

    # Główna wiadomość — same nagłówki
    lines = [f"📰 *Nowości branżowe — {now.strftime('%d.%m.%Y')}*\n"]
    for i, p in enumerate(points, 1):
        emoji = _platform_emoji(p.get("platform", ""))
        lines.append(f"{i}. {emoji} *{p.get('platform', '')}* — {p.get('headline', '')}")
    lines.append(f"\n_Wygenerowano przez Sebol • {now.strftime('%d.%m.%Y %H:%M')} | szczegóły w wątku_ 🧵")

    main_text = "\n".join(lines)
    return main_text, points


def weekly_industry_news():
    """Wysyła tygodniowy digest nowości na kanał #media. Poniedziałek 9:00."""
    if not MEDIA_CHANNEL_ID:
        logger.warning("⚠️  MEDIA_CHANNEL_ID nie ustawiony — pominięto industry news digest.")
        return
    try:
        logger.info("📰 Generuję tygodniowy digest nowości branżowych...")
        main_text, points = generate_industry_news_digest()

        # Główna wiadomość
        resp = _ctx.app.client.chat_postMessage(channel=MEDIA_CHANNEL_ID, text=main_text)
        thread_ts = resp["ts"]

        # Wątek — każdy punkt osobno
        for i, p in enumerate(points, 1):
            emoji = _platform_emoji(p.get("platform", ""))
            url = p.get("url", "")
            thread_msg = (
                f"{emoji} *{i}. {p.get('platform', '')} — {p.get('headline', '')}*\n\n"
                f"{p.get('detail', '')}"
            )
            if url:
                thread_msg += f"\n\n🔗 {url}"
            _ctx.app.client.chat_postMessage(
                channel=MEDIA_CHANNEL_ID,
                thread_ts=thread_ts,
                text=thread_msg,
            )

        logger.info(f"✅ Industry news digest wysłany ({len(points)} punktów w wątku)!")
    except Exception as e:
        logger.error(f"❌ Błąd weekly_industry_news: {e}")
