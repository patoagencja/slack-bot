"""
Weekly industry news digest — poniedziałek 9:00.
Zbiera nowości z Meta Ads, Google Ads, TikTok Ads oraz AI tools,
kondensuje je przez Claude i wysyła na kanał #media.
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
- tylko potwierdzone informacje (testy/zapowiedzi oznacz w treści punktu),
- maksymalnie 8 punktów.
{exclusion_block}
Format wiadomości:
📰 Nowości branżowe — {data}

1. *Platforma* — co się zmieniło i dlaczego ważne. (test/rollout/zapowiedź jeśli dotyczy)
2. ...

🔗 Źródła:
• link1
• link2

Zwróć WYŁĄCZNIE gotową wiadomość na Slacka, bez żadnego wstępu, komentarzy ani wprowadzenia."""


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


def _extract_urls(text: str) -> list[str]:
    return re.findall(r'https?://[^\s\)\]>]+', text)


def _record_urls(urls: list[str]) -> None:
    entries = _load_published()
    today = datetime.now().strftime('%Y-%m-%d')
    existing = {e["url"] for e in entries}
    for url in urls:
        if url not in existing:
            entries.append({"url": url, "date": today})
    _save_published(entries)


# ── Core logic ──────────────────────────────────────────────────────────────────

def _fetch_with_web_search(prompt: str) -> str:
    """Wywołuje Claude z narzędziem web_search i zwraca skondensowaną odpowiedź."""
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


def generate_industry_news_digest() -> str:
    """Generuje tygodniowy digest nowości branżowych z deduplicacją."""
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

    prompt = NEWS_PROMPT.format(
        data=now.strftime('%d.%m.%Y'),
        exclusion_block=exclusion_block,
    )

    try:
        digest = _fetch_with_web_search(prompt)
    except Exception as e:
        logger.error(f"Błąd pobierania nowości: {e}")
        digest = f"❌ Nie udało się wygenerować digestu: {e}"
    else:
        _record_urls(_extract_urls(digest))

    return digest + f"\n\n_Wygenerowano przez Sebol • {now.strftime('%d.%m.%Y %H:%M')}_"


def weekly_industry_news():
    """Wysyła tygodniowy digest nowości na kanał #media. Poniedziałek 9:00."""
    if not MEDIA_CHANNEL_ID:
        logger.warning("⚠️  MEDIA_CHANNEL_ID nie ustawiony — pominięto industry news digest.")
        return
    try:
        logger.info("📰 Generuję tygodniowy digest nowości branżowych...")
        digest = generate_industry_news_digest()
        _ctx.app.client.chat_postMessage(channel=MEDIA_CHANNEL_ID, text=digest)
        logger.info("✅ Industry news digest wysłany na #media!")
    except Exception as e:
        logger.error(f"❌ Błąd weekly_industry_news: {e}")
