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
        model="claude-opus-4-5",
        max_tokens=2000,
        system=LINKEDIN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": topic}],
    )
    return response.content[0].text


# ── Grafika do posta — DALL-E 3 z rotującymi stylami ─────────────────────────

_IMAGE_STYLES = [
    {
        "name": "bold_typography",
        "desc": "Tylko tekst. Jedno zdanie z posta jako ogromny napis na ciemnym tle. Brutalistyczna typografia, kontrast, zero ozdób. Styl: NY Times Magazine cover meets tech poster.",
        "template": "Bold typographic poster, dark background, one short punchy quote from the post in huge white sans-serif letters filling the frame, brutalist design, high contrast, no people, 1080x1080",
    },
    {
        "name": "cinematic_scene",
        "desc": "Dramatyczna, filmowa scena nawiązująca do tematu posta. Photorealistic. Jedno mocne ujęcie.",
        "template": "Cinematic photorealistic scene, dramatic lighting, moody atmosphere, relates to: {topic}. Film still quality, shallow depth of field, 1080x1080",
    },
    {
        "name": "sebol_mascot",
        "desc": "Sebol (robot w szarej bluzie Adidas) w akcji nawiązującej do tematu posta.",
        "template": "Cartoon robot mascot wearing grey Adidas hoodie with white stripes, grey Adidas sweatpants, white sneakers, glowing blue rectangular eyes, dark navy metallic body. The robot is: {action}. Flat illustration style, dark background #0A1520, blue accent #4A90D9, 1080x1080",
    },
    {
        "name": "abstract_data",
        "desc": "Abstrakcyjna wizualizacja danych, sieci neuronowe, przepływy. Kolorowe, energetyczne.",
        "template": "Abstract digital art, flowing data streams, neural network visualization, neon colors (purple, blue, pink) on black background, dynamic energy, relates to AI and marketing analytics, no text, 1080x1080",
    },
    {
        "name": "neon_cyberpunk",
        "desc": "Neon, cyberpunk, miasto nocą. Klimat tech + agencja. Mocny mood.",
        "template": "Cyberpunk aesthetic, neon lights, night city reflections, purple and blue neon signs, rain on glass, futuristic atmosphere, relates to: {topic}, no people visible, 1080x1080",
    },
    {
        "name": "meme_format",
        "desc": "Rozpoznawalny format memowy zaadaptowany do B2B / AI / marketingu. Humor branżowy.",
        "template": "Clean meme format, white background with bold Impact or Arial Black text, top and bottom captions, business/marketing/AI humor theme about: {topic}, professional but funny, 1080x1080",
    },
    {
        "name": "split_before_after",
        "desc": "Dwie połówki — przed/po, stare/nowe, manual/AI. Silny kontrast wizualny.",
        "template": "Split screen composition, left side shows old/manual/chaos (desaturated, messy), right side shows new/automated/clean (vibrant, organized), divided by sharp diagonal line, theme: {topic}, no text needed, 1080x1080",
    },
    {
        "name": "minimal_stat",
        "desc": "Jedna duża liczba lub fakt na czystym tle. Minimalizm. Mocny przekaz jedną liczbą.",
        "template": "Minimalist design, one huge statistic or number centered on clean dark background, small label below, premium feel, relates to: {topic}, geometric accents in electric blue, 1080x1080",
    },
]


def _pick_image_style(post_text: str, topic: str) -> dict:
    """Claude dobiera najlepszy styl grafiki do treści posta."""
    styles_desc = "\n".join(
        f"{i+1}. [{s['name']}] {s['desc']}"
        for i, s in enumerate(_IMAGE_STYLES)
    )
    resp = _ctx.claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{
            "role": "user",
            "content": (
                f"Post LinkedIn:\n{post_text}\n\nTemat: {topic}\n\n"
                f"Dostępne style grafiki:\n{styles_desc}\n\n"
                "Wybierz JEDEN numer stylu który będzie najbardziej catchy dla tego konkretnego posta. "
                "Odpowiedz TYLKO cyfrą (1-8)."
            )
        }]
    )
    try:
        idx = int(resp.content[0].text.strip()) - 1
        return _IMAGE_STYLES[max(0, min(idx, len(_IMAGE_STYLES) - 1))]
    except Exception:
        import random
        return random.choice(_IMAGE_STYLES)


def _build_dalle_prompt(style: dict, post_text: str, topic: str) -> str:
    """Buduje finalny prompt do DALL-E na podstawie stylu i treści posta."""
    # Wyciągnij pierwsze zdanie posta jako potencjalny cytat
    first_line = post_text.split("\n")[0][:120] if post_text else topic

    # Ustal action dla Sebola
    action_map = {
        "prezentacja": "pointing at a floating PPTX presentation in the air",
        "raport": "holding a glowing report document",
        "kampania": "clicking a big launch button",
        "ai": "thinking with circuit patterns in its head",
        "linkedin": "typing on a keyboard with LinkedIn logo floating above",
        "analiza": "looking at colorful charts and graphs",
    }
    action = "working on a laptop with code on the screen"
    for keyword, act in action_map.items():
        if keyword in (post_text + topic).lower():
            action = act
            break

    template = style["template"]
    prompt = (
        template
        .replace("{topic}", topic[:100])
        .replace("{action}", action)
        .replace("{quote}", first_line)
    )
    return prompt


def generate_linkedin_image(post_text: str, topic: str) -> bytes | None:
    """
    Generuje grafikę LinkedIn przez DALL-E 3.
    Zwraca bajty PNG lub None jeśli błąd.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("Brak OPENAI_API_KEY — pomijam generowanie grafiki")
        return None

    try:
        style = _pick_image_style(post_text, topic)
        prompt = _build_dalle_prompt(style, post_text, topic)
        logger.info(f"Generuję grafikę LinkedIn, styl: {style['name']}")

        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
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
