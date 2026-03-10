"""
Weekly industry news digest — poniedziałek 9:00.
Zbiera nowości z Meta Ads, Google Ads, TikTok Ads oraz AI tools,
kondensuje je przez Claude i wysyła na kanał #media.
"""
import os
import logging
from datetime import datetime

import _ctx

logger = logging.getLogger(__name__)

MEDIA_CHANNEL_ID = os.environ.get("MEDIA_CHANNEL_ID", "")

AD_NEWS_PROMPT = """Przeszukaj internet i znajdź najnowsze nowości (z ostatnich 7 dni) dotyczące:
1. Meta Ads (Facebook Ads, Instagram Ads) — nowe funkcje, zmiany w algorytmie, aktualizacje platformy
2. Google Ads — nowe funkcje, zmiany w kampaniach, aktualizacje
3. TikTok Ads — nowe formaty, zmiany, aktualizacje platformy

Dla każdej platformy podaj max 3 najważniejsze nowości w formacie:
• [Krótki tytuł] — [1-2 zdania opisu co się zmieniło i dlaczego to ważne dla reklamodawców]

Odpowiedz po polsku. Bądź konkretny i praktyczny — skupiaj się na rzeczach, które mają realny wpływ na prowadzenie kampanii."""

AI_NEWS_PROMPT = """Przeszukaj internet i znajdź najnowsze nowości (z ostatnich 7 dni) dotyczące:
- Nowych narzędzi AI dla marketerów i agencji reklamowych
- Aktualizacji istniejących narzędzi AI (ChatGPT, Claude, Gemini, Midjourney, itp.)
- Nowych funkcji AI w platformach reklamowych (Meta AI, Google AI features, itp.)
- Ciekawych zastosowań AI w digital marketingu

Podaj max 4 najważniejsze nowości w formacie:
• [Nazwa narzędzia / tytuł] — [1-2 zdania opisu co nowego i jak można to wykorzystać w pracy]

Odpowiedz po polsku. Skup się na praktycznych zastosowaniach w agencji marketingowej."""


def _fetch_with_web_search(prompt: str) -> str:
    """Wywołuje Claude z narzędziem web_search i zwraca skondensowaną odpowiedź."""
    response = _ctx.claude.messages.create(
        model="claude-sonnet-4-5-20251001",
        max_tokens=1500,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        }],
        messages=[{"role": "user", "content": prompt}],
    )
    # Zbierz tekst ze wszystkich bloków tekstowych w odpowiedzi
    parts = [block.text for block in response.content if hasattr(block, "text")]
    return "\n".join(parts).strip()


def generate_industry_news_digest() -> str:
    """Generuje tygodniowy digest nowości branżowych."""
    now = datetime.now()
    week_label = now.strftime("%d.%m.%Y")

    logger.info("🔍 Szukam nowości reklamowych...")
    try:
        ad_news = _fetch_with_web_search(AD_NEWS_PROMPT)
    except Exception as e:
        logger.error(f"Błąd pobierania nowości reklamowych: {e}")
        ad_news = "_Nie udało się pobrać nowości reklamowych._"

    logger.info("🤖 Szukam nowości AI...")
    try:
        ai_news = _fetch_with_web_search(AI_NEWS_PROMPT)
    except Exception as e:
        logger.error(f"Błąd pobierania nowości AI: {e}")
        ai_news = "_Nie udało się pobrać nowości AI._"

    digest = (
        f"📰 *Tygodniowy przegląd branżowy* | {week_label}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📢 *Nowości reklamowe — Meta / Google / TikTok Ads*\n\n"
        f"{ad_news}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🤖 *AI Tools & Features*\n\n"
        f"{ai_news}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Digest wygenerowany automatycznie przez Sebol • {now.strftime('%d.%m.%Y %H:%M')}_"
    )
    return digest


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
