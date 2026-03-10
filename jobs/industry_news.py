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

MEDIA_CHANNEL_ID = os.environ.get("MEDIA_CHANNEL_ID", "C0AKFBL2JR1")

NEWS_PROMPT = """Przeszukaj internet i przygotuj cotygodniowy raport na Slacka po polsku o najważniejszych nowościach z ostatnich 7 dni w:
- Meta Ads
- Google Ads
- TikTok Ads
- AI w reklamie

Skup się na zmianach, które mają praktyczne znaczenie dla performance marketingu: nowe funkcje, rollouty, testy, zmiany w targetowaniu, atrybucji, automatyzacji, kreacjach, raportowaniu i zastosowaniu AI w reklamie.

Zasady:
- korzystaj głównie z oficjalnych źródeł i renomowanych mediów branżowych,
- nie duplikuj tych samych newsów,
- nie dodawaj plotek bez potwierdzenia,
- pokazuj tylko najważniejsze i najbardziej użyteczne informacje,
- przy każdym newsie podaj: co się zmieniło, dlaczego to ważne, kogo dotyczy, status, rekomendację i źródło,
- jeśli coś jest tylko testem lub zapowiedzią, zaznacz to wyraźnie.

Struktura wiadomości:
1. Nagłówek z datą
2. *SKRÓT TYGODNIA* — ponumerowana lista wszystkich newsów, każdy w jednym zdaniu (samo sedno: co i dlaczego ważne). Bez rozwinięć.
3. Rozwinięcia w kolejności z listy:
   - numer i tytuł newsa
   - co się zmieniło
   - dlaczego ważne / kogo dotyczy
   - status (rollout / test / zapowiedź)
   - rekomendacja
   - źródło (link)
4. Co warto przetestować
5. Co to oznacza dla naszego zespołu

Styl:
- konkretny,
- krótki,
- praktyczny,
- gotowy do wklejenia na Slacka,
- bez tabel,
- bez lania wody.

Zwróć tylko finalną wiadomość na Slack."""


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
    # Zbierz tekst ze wszystkich bloków tekstowych w odpowiedzi
    parts = [block.text for block in response.content if hasattr(block, "text")]
    return "\n".join(parts).strip()


def generate_industry_news_digest() -> str:
    """Generuje tygodniowy digest nowości branżowych."""
    now = datetime.now()
    logger.info("🔍 Szukam nowości branżowych...")
    try:
        digest = _fetch_with_web_search(NEWS_PROMPT)
    except Exception as e:
        logger.error(f"Błąd pobierania nowości: {e}")
        digest = f"❌ Nie udało się wygenerować digestu: {e}"
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
