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
    """Zwraca listę {headline, platform, date} z ostatnich 30 dni.
    Źródło prawdy: kanał #media na Slacku (odporne na restarty Rendera).
    Fallback: lokalny plik JSON.
    """
    # Próba 1: odczyt z kanału #media (nie ginie po deploy)
    slack_entries = _load_published_from_slack()
    if slack_entries:
        # Odśwież lokalny cache żeby następne wywołanie było szybsze
        _save_published(slack_entries)
        return slack_entries

    # Fallback: lokalny plik
    if not os.path.exists(PUBLISHED_NEWS_FILE):
        return []
    try:
        with open(PUBLISHED_NEWS_FILE) as f:
            entries = json.load(f)
        cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        return [e for e in entries if e.get("date", "") >= cutoff]
    except Exception:
        return []


def _load_published_from_slack() -> list[dict]:
    """Czyta ostatnie 300 wiadomości z #media i wyciąga opublikowane nagłówki."""
    try:
        result = _ctx.app.client.conversations_history(
            channel=MEDIA_CHANNEL_ID,
            limit=300,
        )
        entries = []
        cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        for msg in result.get("messages", []):
            text = msg.get("text", "")
            # Nagłówki digestu mają format: "1. 🔵 *Meta Ads* — Treść nagłówka"
            matches = re.findall(
                r'\d+\.\s+[🔵🟡⚫🤖📌]\s+\*([^*]+)\*\s+—\s+(.+)',
                text,
            )
            if matches:
                # Wyznacz datę z timestamp wiadomości
                ts = float(msg.get("ts", 0))
                msg_date = datetime.fromtimestamp(ts).strftime('%Y-%m-%d') if ts else ""
                if msg_date >= cutoff:
                    for platform, headline in matches:
                        entries.append({
                            "headline": headline.strip(),
                            "platform": platform.strip(),
                            "date": msg_date,
                        })
        return entries
    except Exception as e:
        logger.warning(f"Nie udało się wczytać historii newsów ze Slack: {e}")
        return []


def _save_published(entries: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(PUBLISHED_NEWS_FILE), exist_ok=True)
        with open(PUBLISHED_NEWS_FILE, "w") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        logger.error(f"Błąd zapisu published_news: {e}")


def _record_points(points: list[dict]) -> None:
    """Zapisuje nagłówki + platformę opublikowanych newsów."""
    entries = _load_published()
    today = datetime.now().strftime('%Y-%m-%d')
    for p in points:
        headline = p.get("headline", "").strip()
        if headline:
            entries.append({
                "headline": headline,
                "platform": p.get("platform", ""),
                "date": today,
            })
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
    # Collect all text blocks — Claude may interleave tool_use and text blocks
    parts = []
    for block in response.content:
        if hasattr(block, "text") and block.text:
            parts.append(block.text)
    raw = "\n".join(parts).strip()
    logger.debug("_fetch_with_web_search raw (first 500): %s", raw[:500])
    return raw


def _parse_points(raw: str) -> list[dict]:
    """Wyciąga listę punktów z odpowiedzi Claude'a — kilka strategii parsowania."""
    # Strategy 1: strip markdown fences, find JSON object
    cleaned = re.sub(r'```(?:json)?\s*', '', raw).strip()
    cleaned = re.sub(r'```\s*$', '', cleaned).strip()

    for text in [cleaned, raw]:
        # Try outermost JSON object
        try:
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                data = json.loads(match.group())
                points = data.get("points", [])
                if points:
                    return [p for p in points if p.get("headline")]
        except Exception:
            pass

        # Try JSON array directly (if Claude returned just the array)
        try:
            match = re.search(r'\[[\s\S]*\]', text)
            if match:
                points = json.loads(match.group())
                if isinstance(points, list) and points and points[0].get("headline"):
                    return points
        except Exception:
            pass

    # Strategy 2: Claude didn't return JSON — ask it to reformat what it found
    logger.warning("Pierwsze parsowanie nieudane — próbuję re-format przez Claude")
    try:
        resp = _ctx.claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": (
                    "Poniżej jest tekst z informacjami o nowościach branżowych. "
                    "Wyciągnij z niego maksymalnie 8 punktów i zwróć TYLKO JSON w formacie:\n"
                    '{"points":[{"platform":"Meta Ads","headline":"...","detail":"...","url":"..."}]}\n\n'
                    f"Tekst:\n{raw[:3000]}"
                ),
            }],
        )
        reformat = resp.content[0].text.strip()
        cleaned2 = re.sub(r'```(?:json)?\s*', '', reformat).strip()
        match = re.search(r'\{[\s\S]*\}', cleaned2)
        if match:
            data = json.loads(match.group())
            points = data.get("points", [])
            if points:
                return [p for p in points if p.get("headline")]
    except Exception as e:
        logger.error("Re-format fallback failed: %s", e)

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
        topics_list = "\n".join(
            f"• [{e.get('platform', '')}] {e.get('headline', '')}"
            for e in published
            if e.get("headline")
        )
        exclusion_block = (
            f"\nPONIŻSZE TEMATY zostały już opublikowane w poprzednich tygodniach — "
            f"NIE powtarzaj ich ani podobnych zagadnień:\n"
            f"{topics_list}\n"
            f"Znajdź wyłącznie nowe tematy, których nie ma na powyższej liście.\n"
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
        logger.warning("Brak punktów w odpowiedzi — nie udało się sparsować newsów")
        return f"⚠️ Nie udało się wygenerować digestu newsów — Claude zwrócił nieoczekiwany format odpowiedzi.\n\n_Sebol • {now.strftime('%d.%m.%Y %H:%M')}_", []

    _record_points(points)

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
