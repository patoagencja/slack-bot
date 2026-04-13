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

# WAŻNE: DALL-E nie umie renderować tekstu — ŻADEN styl nie może polegać na literach/napisach w obrazku.
# Wszystkie style są czysto wizualne — metafory, sceny, abstrakcje.
_IMAGE_STYLES = [
    {
        "name": "cinematic_human",
        "desc": "Dramatyczna, filmowa scena z człowiekiem przy pracy. Pasuje do postów 'behind the scenes' i storytellingu.",
        "template": "Cinematic photorealistic photo, one person working late at night in a dark modern office, multiple screens glowing with dashboards and charts, dramatic blue and purple light, moody atmosphere, shot from behind or side angle, NO TEXT, NO LETTERS anywhere, 1024x1024",
    },
    {
        "name": "sebol_robot",
        "desc": "Sebol-robot w akcji. Pasuje do postów o funkcjach Sebola i demo.",
        "template": "3D cartoon robot character in grey Adidas hoodie with white stripes, grey sweatpants, white sneakers, glowing rectangular blue eyes, dark metallic navy body, {action}, flat cel-shaded illustration, dark navy background (#0A1520), bold blue accent lighting, NO TEXT NO LETTERS, square 1024x1024",
    },
    {
        "name": "abstract_flow",
        "desc": "Abstrakcyjna wizualizacja przepływu danych / automatyzacji. Energetyczna, kolorowa.",
        "template": "Abstract digital art, glowing data streams flowing through dark space, interconnected nodes and geometric shapes, electric purple and blue and cyan gradient, sense of speed and automation, zero text, zero letters, purely visual, 1024x1024",
    },
    {
        "name": "split_contrast",
        "desc": "Dwie połówki — chaos vs porządek, ręczne vs AI, stare vs nowe. Silny kontrast bez tekstu.",
        "template": "Split composition square image, LEFT half: chaotic messy desk with papers everywhere, stressed person, warm desaturated tones, RIGHT half: clean minimalist workspace with glowing screens, calm organized, cool blue tones, sharp diagonal dividing line, NO TEXT NO WORDS, purely visual metaphor, 1024x1024",
    },
    {
        "name": "neon_tech",
        "desc": "Neonowy, cyberpunkowy klimat tech. Miasto nocą, refleksy, fiolet/niebieski. Mocny mood.",
        "template": "Cyberpunk aesthetic square photo, rainy night city street reflection in puddles, purple and blue neon store signs (generic geometric shapes, NOT readable words), wet asphalt, bokeh lights, cinematic mood, NO READABLE TEXT anywhere, 1024x1024",
    },
    {
        "name": "flat_isometric",
        "desc": "Izometryczna ilustracja flat design — biuro, dashboard, przepływ pracy. Profesjonalne, czyste.",
        "template": "Isometric flat design illustration, small office scene with computer screens showing colorful charts and graphs (no readable numbers), tiny human figures working, clean lines, vibrant colors (blue purple orange), white background, modern tech company vibe, NO TEXT NO LETTERS, 1024x1024",
    },
    {
        "name": "dramatic_light",
        "desc": "Jeden obiekt w dramatycznym świetle studyjnym. Minimalistyczne, premium, przyciąga wzrok.",
        "template": "Dramatic studio lighting, single object centered: a sleek laptop or smartphone with glowing screen showing colorful abstract graphs (not readable), dark background, rim light in electric blue, luxury product photography style, NO TEXT, minimalist, 1024x1024",
    },
    {
        "name": "robot_human_collab",
        "desc": "Robot i człowiek pracują razem — metafora AI + człowiek. Pozytywny, nowoczesny.",
        "template": "Photorealistic illustration, human hand and robotic hand pointing at the same glowing holographic dashboard with colorful abstract data visualizations, warm and cool lighting contrast, sense of collaboration, NO TEXT NO LETTERS on any surface, cinematic, 1024x1024",
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
    # Ustal action dla Sebola (czysto wizualne opisy bez tekstu)
    action_map = {
        "prezentacja": "holding a large glowing slide deck floating in the air",
        "raport": "carrying a glowing document with colorful bar charts on it",
        "kampania": "pressing a large glowing launch button with both hands",
        "ai": "surrounded by glowing neural network nodes connecting to its head",
        "linkedin": "sitting at a desk typing on a glowing keyboard",
        "analiza": "pointing at floating colorful pie charts and bar graphs",
        "pptx": "holding a large glowing slide deck floating in the air",
        "automatyz": "pulling levers on a control panel with colorful status lights",
        "budżet": "looking at a large glowing coin stack with an upward arrow",
    }
    action = "working on a glowing laptop in a dark room"
    combined = (post_text + " " + topic).lower()
    for keyword, act in action_map.items():
        if keyword in combined:
            action = act
            break

    template = style["template"]
    prompt = (
        template
        .replace("{topic}", topic[:80])
        .replace("{action}", action)
    )
    # Upewnij się że zawsze jest zakaz tekstu na końcu
    if "NO TEXT" not in prompt.upper():
        prompt += ", absolutely NO TEXT, NO LETTERS, NO WORDS anywhere in the image"
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
        import base64
        client = OpenAI(api_key=api_key)

        # gpt-image-1 = model używany przez ChatGPT — znacznie lepszy od dall-e-3
        try:
            resp = client.images.generate(
                model="gpt-image-1",
                prompt=prompt,
                size="1024x1024",
                quality="high",
                n=1,
            )
            # gpt-image-1 zwraca base64, nie URL
            b64 = resp.data[0].b64_json
            if b64:
                return base64.b64decode(b64)
        except Exception as _e1:
            logger.warning(f"gpt-image-1 failed ({_e1}), fallback do dall-e-3...")
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
