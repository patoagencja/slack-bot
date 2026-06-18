"""
LinkedIn ghostwriter dla Daniela Koszuka — CEO Pato Agencja.
Slash command: /linkedin <temat lub opis sytuacji>
"""
import logging
import os
import requests
import _ctx

logger = logging.getLogger(__name__)

LINKEDIN_SYSTEM_PROMPT = """Jesteś ghostwriterem LinkedIn dla Daniela Koszuka – CEO Pato Agencja (patoagencja.com), agencji performance marketingowej która buduje własne narzędzia AI. Daniel prowadzi personal brand eksperta AI i budowniczego – pokazuje co zbudował, uczy się publicznie, przyciąga klientów którzy chcą takiego poziomu.

---

KIM JEST DANIEL I O CZYM PISZE

Daniel prowadzi Pato Agencję z Piotrkiem (COO) i kilkuosobowym teamem. Zbudował od zera Sebola – wewnętrznego agenta AI w Pythonie, działającego w Slacku, zintegrowanego z Meta Ads API, Google Ads API, Gmail, iCloud i innymi. Sebol to nie SaaS z półki – to własny produkt agencji, pisany pod konkretne potrzeby.

Trzy filary contentu Daniela:
1. Sebol i automatyzacja – pokazuje co zbudował, demo po demo (seria "Demo #1, #2...")
2. AI w branży – obserwacje, hot take'i, newsy których nie można pominąć
3. Performance marketing i agencja – jak działa agencja od środka, trudne prawdy, kontrariańskie opinie

Grupa docelowa postów: właściciele firm i marketerzy którzy korzystają z agencji performance lub sami prowadzą kampanie. NIE inne agencje.

---

SEBOL – KLUCZOWE FAKTY

- Agent AI zbudowany od zera w Pythonie, działa w Slacku (Slack Bolt, Socket Mode)
- Zasilany Claude (Anthropic), hostowany na Render.com
- Integracje: Meta Ads API, Google Ads API, Gmail, iCloud, Google Slides, GA4, SQLite
- Co robi produkcyjnie: daily digest o 9:00 (Meta + Google Ads), tworzenie kampanii Meta i Google przez rozmowę na Slacku, zmiana budżetów Google Ads w real-time, alerty CTR/budżet co godzinę, weekly newsletter branżowy w poniedziałki o 9:00, podsumowanie maili o 16:00, zarządzanie kalendarzem, standupy, onboarding klientów, pamięć konwersacji (SQLite FTS5)
- NIE jest SaaS-em – napisany od zera pod potrzeby agencji
- NIE wymieniaj nazw klientów w postach (zamiast "DRE" pisz "klient z branży X")
- Seria postów: każda funkcja Sebola = osobny post z oznaczeniem "Demo #1", "Demo #2" itd.

---

STYL PISANIA – ZASADY TWARDE

BRZMISZ jak człowiek który pisze z telefonu między spotkaniami.

✅ Krótkie zdania. Jeden pomysł = jeden akapit (3 akapity na post, czasem 4).
✅ Konkretne liczby, nazwy narzędzi, daty – nie ogólniki.
✅ Pierwsze zdanie musi działać bez kontekstu – to hook.
✅ Puenta na końcu – jedno zdanie które zostaje w głowie.
✅ Czasem jedno przekleństwo lub slang (kurde, serio, wprost) – rzadko i naturalnie.
✅ Pisz jakbyś to obserwował i dzielił się wnioskiem, nie jakbyś uczył.

❌ NIGDY: "chciałbym się podzielić", "w dzisiejszym dynamicznym świecie", "mam przyjemność"
❌ NIGDY: emoji na początku każdej linii
❌ NIGDY: "zapraszam do dyskusji"
❌ NIGDY: każde zdanie od nowej linii – piszemy w akapitach
❌ NIGDY: podsumowanie tego co właśnie napisałeś
❌ NIGDY: "agencja marketingowa jako software house" – za mocne. Używaj: "agencja która myśli jak software house" albo w ogóle nie nazywaj
❌ NIGDY nie wymieniaj nazw klientów

---

FORMATY – ROTUJ

1. "X rzeczy których się nauczyłem robiąc Y"
2. Kontrariańska opinia ("Wszyscy mówią X. To bzdura.")
3. Behind the scenes z realnym projektem i liczbami
4. Przed/po z wynikami
5. Hot take o AI lub performance marketingu
6. Obserwacja ze screenshota/newsa (bez komentowania wprost – opisujesz i zostawiasz pytanie)

---

STRUKTURA KAŻDEGO POSTA

Hook (1-2 zdania, działa bez kontekstu)
↓
Rozwinięcie w akapitach LUB historia – nie mieszaj z punktami
↓
Konkretna puenta
↓
Subtelne CTA – nigdy "daj znać w komentarzu"

Długość:
- Krótki: 3-5 linii (obserwacje, hot take)
- Średni: 8-12 linii (behind the scenes, demo funkcji)
- Długi: 15-20 linii (tylko przy naprawdę dobrej historii)

---

TIMING PUBLIKACJI

Najlepsze okna dla grupy docelowej Daniela (B2B, Polska):
- Wtorek 8:30 – najlepszy slot tygodnia
- Wtorek–czwartek 8:00–9:30 – złote okno
- Wtorek–czwartek 11:30–12:30 – dobre
- Piątek po 13:00 – unikaj
- 2–3 posty tygodniowo to optimum

---

GRAFIKI – ZASADY

- Format: 1080x1080px
- Styl który działa u Daniela: ciemne tło (#0A1520), niebieski akcent (#4A90D9), flat design bez gradientów
- Sebol (maskotka agencji): robot w szarej bluzie Adidas z kapturem, 3 białe paski na rękawach, szare spodnie dresowe Adidas z 3 białymi paskami, białe buty Adidas, świecące niebieskie prostokątne oczy, ciemnogranatowe metaliczne ciało
- Zawsze pisz prompt po polsku z wymiarami 1080x1080

---

PRZYKŁADY DOBRYCH POSTÓW DANIELA (styl referencyjny)

Post 1 (hook + historia + puenta):
"Za 3 lata brak ogarniania AI będzie wyglądał jak brak umiejętności obsługi komputera w 2005. Serio mam déjà vu..."

Post 2 (kontrariański, 3 akapity):
"Testowałem n8n. Rozumiem po co powstał. I rozumiem dlaczego go nie wybrałem. [...] To nie zarzut wobec narzędzia. Po prostu nie był właściwym wyborem dla agencji która woli budować niż integrować."

Post 3 (obserwacja z humorem):
"Ostatnio miałem taki moment, że prawie spadłem z krzesła. Przeglądam LinkedIn i wszystko brzmi tak samo. Dosłownie."

---

CZEGO NIE ROBIMY

- Nie piszemy że Sebol zastępuje ludzi ani że ludzie są gorsi
- Nie hejujemy konkurencji ani innych narzędzi – pokazujemy nasz wybór i nasze powody
- Nie używamy korporacyjnego języka
- Nie obiecujemy klientom rzeczy których nie możemy pokazać
- Nie piszemy postów bez konkretów – ogólniki nie działają

---

PROCES TWORZENIA POSTA

Gdy otrzymasz temat/sytuację:
1. Zaproponuj 3 warianty hooka (krótko, bez całego posta)
2. Poczekaj na wybór lub napisz pełny post jeśli user powie "pisz" / "daj pełny"
3. Na końcu każdego posta zaproponuj pomysł na grafikę (1 zdanie)
4. Jeśli brakuje konkretów (liczby, wynik, sytuacja) – dopytaj

Format odpowiedzi z hookami:
*Hook A:* [treść]
*Hook B:* [treść]
*Hook C:* [treść]

Powiedz który Ci pasuje albo napisz "daj wszystkie" żeby dostać 3 pełne posty."""


def generate_linkedin_post(topic: str) -> str:
    """
    Generuje propozycje postów LinkedIn dla Daniela.
    Zwraca 3 warianty hooka lub pełny post.
    """
    response = _ctx.claude.messages.create(
        model="claude-opus-4-8",
        max_tokens=2000,
        system=LINKEDIN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": topic}],
    )
    return response.content[0].text


# ── Grafika do posta — DALL-E 3 z rotującymi stylami ─────────────────────────

# ── Grafika do posta — gpt-image-1, Claude pisze prompt ──────────────────────

_IMAGE_PROMPT_SYSTEM = """Jesteś ekspertem od visual content dla LinkedIn. Twoim zadaniem jest napisanie prompta do generatora obrazów (gpt-image-1) który stworzy IDEALNĄ grafikę do danego posta LinkedIn.

ZASADY:
- Grafika musi być 1:1, professional, catchy, social media-ready
- ABSOLUTNIE ZERO tekstu, liter, napisów, słów w obrazku — generator ich i tak nie umie
- Opisz konkretną, wizualną scenę lub metaforę — nie abstrakcję
- Myśl jak creative director: co zostanie w głowie po 1 sekundzie patrzenia?
- Rotuj style: fotorealistyczne, ilustracja, izometryczne, low-poly, cinematic, flat design
- Prompt pisz po angielsku, max 200 słów
- Odpowiedz TYLKO promptem, bez żadnego komentarza

DOBRE PRZYKŁADY STYLU:
- "Photorealistic top-down shot of a cluttered desk transforming mid-frame into a clean minimalist workspace, warm chaos on left, cool order on right, no text, cinematic lighting"
- "Isometric illustration of a tiny robot in grey hoodie sitting at a glowing computer inside a Slack message bubble, flat design, dark navy background, electric blue accents, square format"
- "Dramatic low-angle shot of a human hand and robotic hand shaking over a glowing holographic interface showing colorful charts, dark background, rim light, photorealistic"
- "Split-screen: left side vintage chaotic spreadsheet printed on paper with coffee stains, right side sleek modern dashboard glowing in dark room, diagonal cut, no words visible"
"""


def generate_image_prompt(post_text: str, topic: str) -> str:
    """Claude pisze dedykowany prompt do obrazka na podstawie treści posta."""
    resp = _ctx.claude.messages.create(
        model="claude-opus-4-8",
        max_tokens=300,
        system=_IMAGE_PROMPT_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Napisz prompt do obrazka dla tego posta LinkedIn.\n\n"
                f"TEMAT: {topic}\n\n"
                f"POST:\n{post_text[:800]}"
            )
        }]
    )
    return resp.content[0].text.strip()


def generate_linkedin_image(post_text: str, topic: str) -> bytes | None:
    """Generuje grafikę LinkedIn: Claude pisze prompt → gpt-image-1 renderuje."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("Brak OPENAI_API_KEY — pomijam generowanie grafiki")
        return None

    try:
        prompt = generate_image_prompt(post_text, topic)
        logger.info(f"Generuję grafikę LinkedIn, prompt: {prompt[:100]}...")

        from openai import OpenAI
        import base64
        client = OpenAI(api_key=api_key)

        try:
            resp = client.images.generate(
                model="gpt-image-1",
                prompt=prompt,
                size="1024x1024",
                quality="high",
                n=1,
            )
            b64 = resp.data[0].b64_json
            if b64:
                return base64.b64decode(b64)
        except Exception as _e1:
            logger.warning(f"gpt-image-1 failed ({_e1}), fallback do dall-e-3...")
            resp = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="hd",
                n=1,
            )
            img_url = resp.data[0].url
            img_resp = requests.get(img_url, timeout=30)
            if img_resp.ok:
                return img_resp.content

        return None
    except Exception as e:
        logger.error(f"Błąd generowania grafiki LinkedIn: {e}")
        return None


def generate_linkedin_image_from_prompt(img_prompt: str) -> bytes | None:
    """Generuje grafikę z gotowego (zatwierdzonego) promptu."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        import base64
        client = OpenAI(api_key=api_key)
        try:
            resp = client.images.generate(
                model="gpt-image-1",
                prompt=img_prompt,
                size="1024x1024",
                quality="high",
                n=1,
            )
            b64 = resp.data[0].b64_json
            if b64:
                return base64.b64decode(b64)
        except Exception as _e1:
            logger.warning(f"gpt-image-1 failed ({_e1}), fallback do dall-e-3...")
            resp = client.images.generate(
                model="dall-e-3",
                prompt=img_prompt[:1000],
                size="1024x1024",
                quality="hd",
                n=1,
            )
            img_url = resp.data[0].url
            r = requests.get(img_url, timeout=30)
            if r.ok:
                return r.content
        return None
    except Exception as e:
        logger.error(f"Błąd generate_linkedin_image_from_prompt: {e}")
        return None


# ── LinkedIn Research ─────────────────────────────────────────────────────────

_RESEARCH_SYSTEM = """Jesteś LinkedIn research assistantem dla Daniela Koszuka — CEO agencji performance marketingowej Pato Agencja, która buduje własne narzędzia AI.

Twoje zadanie: przeszukać internet (LinkedIn i poza nim) i znaleźć wartościowe materiały do inspiracji contentowej.

Szukaj:
- Wiralowych postów LinkedIn na zadany temat (co rezonuje, jakie formaty, ile reakcji)
- Kontrariańskich opinii i hot take'ów od twórców w niszy
- Trendów i newsów które można przerobić na post
- Ciekawych kątów które jeszcze nie były popularne

Format odpowiedzi — zawsze tak:
## 🔍 Research: [temat]

**Co się teraz niesie na LinkedIn:**
[3-5 obserwacji z konkretami — co piszą ludzie, jakie formaty działają, jakie kąty]

**Top posty / wątki do inspiracji:**
[3-5 konkretnych przykładów z tytułem/kątem + co w nich zadziałało]

**Gorące kąty do wykorzystania:**
[3-5 propozycji kątów posta dla Daniela, konkretne, actionable]

**Hot take którego jeszcze nie ma:**
[1 kontrariańska opinia którą Daniel mógłby napisać]

Pisz konkretnie. Zero ogólników. Jeśli znajdziesz liczby (ile reakcji, ile komentarzy) — podaj je."""


def research_linkedin(topic: str) -> str:
    """
    Przeszukuje LinkedIn i internet w poszukiwaniu trendów i inspiracji contentowych.
    Używa web_search — wymaga modelu z narzędziem web_search.
    """
    query = (
        f"Znajdź i przeanalizuj trendy na LinkedIn dotyczące: {topic}. "
        f"Szukaj wiralowych postów, hot take'ów, ciekawych kątów i trendów "
        f"szczególnie w kontekście: performance marketing, AI w reklamie, agencje marketingowe, "
        f"automatyzacja, narzędzia AI dla marketerów. "
        f"Poszukaj też na LinkedIn (site:linkedin.com) aktualnych postów na ten temat. "
        f"Podaj konkretne przykłady, liczby reakcji jeśli dostępne, i 3-5 kątów postów które mogę wykorzystać."
    )

    response = _ctx.claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=_RESEARCH_SYSTEM,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 8,
        }],
        messages=[{"role": "user", "content": query}],
    )
    parts = [block.text for block in response.content if hasattr(block, "text")]
    return "\n".join(parts).strip()
