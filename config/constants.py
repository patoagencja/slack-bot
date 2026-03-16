"""
All project-wide constants for Sebol bot.
No imports from other project modules — safe to import from anywhere.
"""
import os
import json

# Base directory = slack-bot/  (one level up from config/)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── FILE PATHS ─────────────────────────────────────────────────────────────────
AVAILABILITY_FILE     = os.path.join(_BASE_DIR, "data", "team_availability.json")
REQUESTS_FILE         = os.path.join(_BASE_DIR, "data", "team_requests.json")
ONBOARDING_FILE       = os.path.join(_BASE_DIR, "data", "onboardings.json")
STANDUP_FILE          = os.path.join(_BASE_DIR, "data", "standup.json")
HISTORY_FILE          = os.path.join(_BASE_DIR, "data", "campaign_history.json")
_DIGEST_LAST_SENT_FILE = os.path.join(_BASE_DIR, "data", "digest_last_sent.json")
PUBLISHED_NEWS_FILE   = os.path.join(_BASE_DIR, "data", "published_news.json")

HISTORY_RETENTION_DAYS = 90
_DIGEST_INTERVAL_DAYS  = 3


# ── CAMPAIGN CREATION CHANNEL ─────────────────────────────────────────────────
# Bot answers every message on this channel (no @mention needed), but only in threads.
CAMPAIGN_CHANNEL_ID = os.environ.get("CAMPAIGN_CHANNEL_ID", "")

# ── STANDUP ────────────────────────────────────────────────────────────────────
STANDUP_CHANNEL    = os.environ.get("STANDUP_CHANNEL_ID",
                     os.environ.get("GENERAL_CHANNEL_ID", ""))

STANDUP_QUESTION = (
    "☀️ *Dzień dobry! Szybki standup* (odpowiedz tutaj — skleję o 9:30)\n\n"
    "1️⃣ Co dziś planujesz robić?\n"
    "2️⃣ Jakieś blokery lub czego potrzebujesz od innych?"
)


# ── TEAM MEMBERS ───────────────────────────────────────────────────────────────
TEAM_MEMBERS = [
    {
        "name":     "Daniel",
        "role":     "CEO",
        "slack_id": "UTE1RN6SJ",
        "aliases":  ["daniel", "daniela", "danio", "dan", "daniego", "danka", "dankowi"],
    },
    {
        "name":     "Piotrek",
        "role":     "COO",
        "slack_id": "USZ1MSDUJ",
        "aliases":  ["piotrek", "piotrka", "piotr", "piotra", "piotrkowi", "piotrowi",
                     "piotruś", "pietrek", "pietrka", "pietrkowi"],
    },
    {
        "name":     "Paulina",
        "role":     "pracownik",
        "slack_id": "U05TASHT92S",
        "aliases":  ["paulina", "pauliny", "paulinie", "paulinę", "pauline",
                     "paula", "pauli", "paulie"],
    },
    {
        "name":     "Magda",
        "role":     "pracownik",
        "slack_id": "U05ELG4FHMG",
        "aliases":  ["magda", "magdy", "magdzie", "magdalena", "magdaleny", "magdalenie"],
    },
    {
        "name":     "Ewa",
        "role":     "pracownik",
        "slack_id": "U03011HEDBR",
        "aliases":  ["ewa", "ewy", "ewie", "ewka", "ewki", "ewce"],
    },
    {
        "name":     "Emka",
        "role":     "pracownik",
        "slack_id": "U07ML556LLU",
        "aliases":  ["emka", "emki", "emce", "emma", "em", "emilia", "emilii", "emilię", "emilie"],
    },
]


# ── CLIENT / ADS CONFIG ────────────────────────────────────────────────────────
CLIENT_GOALS = {
    "drzwi dre": "engagement",
    # "inny klient": "conversion",
}

AD_CLIENTS = {
    "dre": {
        "display_name":    "Drzwi DRE",
        "meta_name":       "drzwi dre",
        "google_accounts": ["dre", "dre 2024", "dre 2025"],
        "goal":            "engagement",
        "channel_id":      os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8"),
    },
}

CHANNEL_CLIENT_MAP = {
    os.environ.get("DRE_CHANNEL_ID", "C05GPM4E9B8"): "dre",
}


# ── ONBOARDING CHECKLIST ────────────────────────────────────────────────────────
ONBOARDING_CHECKLIST = [
    {"id": 1,  "emoji": "📋", "name": "Brief klienta — cele, KPI, grupa docelowa"},
    {"id": 2,  "emoji": "💰", "name": "Budżet miesięczny potwierdzony"},
    {"id": 3,  "emoji": "🔷", "name": "Pixel Meta zainstalowany i zweryfikowany"},
    {"id": 4,  "emoji": "🔷", "name": "Dostęp do konta Meta Ads"},
    {"id": 5,  "emoji": "🟡", "name": "Google Tag Manager zainstalowany"},
    {"id": 6,  "emoji": "🟡", "name": "Dostęp do konta Google Ads"},
    {"id": 7,  "emoji": "🟡", "name": "Google Analytics 4 — cele i konwersje"},
    {"id": 8,  "emoji": "🎨", "name": "Materiały kreatywne od klienta dostarczone"},
    {"id": 9,  "emoji": "✍️",  "name": "Copy i treści zatwierdzone"},
    {"id": 10, "emoji": "🚀", "name": "Pierwsze kampanie uruchomione"},
    {"id": 11, "emoji": "📊", "name": "Raportowanie / dashboard skonfigurowany"},
    {"id": 12, "emoji": "✉️",  "name": "Email powitalny do klienta wysłany"},
]


# ── ABSENCE / REQUESTS LABELS ──────────────────────────────────────────────────
ABSENCE_KEYWORDS = [
    "nie będzie", "nie bedzie", "nie ma mnie", "nie będę", "nie bede",
    "nie ma go", "nie ma jej", "nie ma", "nie będzie go", "nie bedzie go",
    "urlop", "wolne", "nieobecn", "będę tylko", "bede tylko",
    "będę od", "bede od", "będę do", "bede do",
    "wychodzę wcześniej", "wychodze wczesniej", "wcześniej wychodzę",
    "zdalnie", "home office", "homeoffice", "choruję", "choruje", "l4",
    "nie przyjdę", "nie przyjde", "spóźnię się", "spoznie sie",
    "przyjdę później", "przyjde pozniej", "późniejszy start",
    "tylko rano", "tylko po południu", "tylko popoludniu",
    "wyjazd", "wyjeżdżam", "wyjezdzam",
    "delegacja", "delegacj", "konferencja", "konferencj",
    "szkolenie", "szkoleni", "targi", "wyjazd służbowy",
    "nie będzie mnie", "nie bedzie mnie", "mnie nie będzie", "mnie nie bedzie",
    "jestem niedostępny", "jestem niedostepny", "niedostępna", "niedostepna",
    "biorę wolne", "biore wolne", "wolny dzień", "wolna",
]

EMPLOYEE_MSG_KEYWORDS = ABSENCE_KEYWORDS + [
    "prośba", "prosba", "chciał", "chcialbym", "chciałabym", "chciałem",
    "czy mogę", "czy moge", "czy możemy", "czy mozemy", "czy możesz",
    "potrzebuję", "potrzebuje", "potrzebna", "potrzebny",
    "chcę", "chce", "wnioskuję", "wniosek",
    "urlop", "wolne", "zakup", "zamówić", "zamowic",
    "dostęp", "dostep", "konto", "licencja",
    "spotkanie", "porozmawiać", "porozmawiac", "umówić", "umowic",
    "problem", "błąd", "blad", "nie działa", "nie dziala",
    "pytanie", "zapytać", "zapytac", "decyzja",
    "podwyżka", "podwyzka", "nadgodziny", "nadgodzin",
    "faktura", "rachunek", "rozliczenie",
]

TYPE_LABELS_ABSENCE = {
    "absent":           "❌ Nieobecna/y cały dzień",
    "morning_only":     "🌅 Tylko rano",
    "afternoon_only":   "🌆 Tylko po południu",
    "late_start":       "🕙 Późniejszy start",
    "early_end":        "🏃 Wcześniejsze wyjście",
    "remote":           "🏠 Praca zdalna",
    "partial":          "⏰ Częściowo dostępna/y",
}

REQUEST_CATEGORY_LABELS = {
    "urlop":     "🏖️ Urlop / czas wolny",
    "zakup":     "🛒 Zakup / sprzęt",
    "dostep":    "🔑 Dostęp / narzędzia",
    "spotkanie": "📆 Spotkanie / rozmowa",
    "problem":   "⚠️ Problem / zgłoszenie",
    "pytanie":   "❓ Pytanie / decyzja",
    "inne":      "📌 Inne",
}


# ── CAMPAIGN CREATOR ────────────────────────────────────────────────────────────

MAX_DAILY_BUDGET = 2000   # PLN/dzień — limit bezpieczeństwa
MAX_TOTAL_BUDGET = 10000  # PLN total — limit bezpieczeństwa

# Parsuj META_AD_ACCOUNT_ID (JSON dict jak w innych narzędziach bota)
# Format: '{"drzwi dre": "act_824677501944646", "zbiorcze": "act_206751433757184"}'
_meta_ad_account_raw = os.environ.get("META_AD_ACCOUNT_ID", "{}")
try:
    _meta_ad_map = json.loads(_meta_ad_account_raw)
except Exception:
    _meta_ad_map = {}

# Meta Ads Account IDs — wspiera klucze "dre" i "drzwi dre" (aliasy)
META_ACCOUNT_IDS = {
    "dre":              _meta_ad_map.get("drzwi dre") or os.environ.get("DRE_META_ACCOUNT_ID", ""),
    "drzwi dre":        _meta_ad_map.get("drzwi dre") or os.environ.get("DRE_META_ACCOUNT_ID", ""),
    "instax":           _meta_ad_map.get("instax")    or os.environ.get("INSTAX_META_ACCOUNT_ID", ""),
    "zbiorcze":         _meta_ad_map.get("zbiorcze")  or os.environ.get("ZBIORCZE_META_ACCOUNT_ID", ""),
    "kampanie zbiorcze":_meta_ad_map.get("kampanie zbiorcze") or _meta_ad_map.get("zbiorcze", ""),
    "m2":               _meta_ad_map.get("m2")        or os.environ.get("M2_META_ACCOUNT_ID", ""),
    "pato":             _meta_ad_map.get("pato")       or os.environ.get("PATO_META_ACCOUNT_ID", ""),
}

# Meta Page IDs per klient
META_PAGE_IDS = {
    "dre":       os.environ.get("DRE_META_PAGE_ID", ""),
    "drzwi dre": os.environ.get("DRE_META_PAGE_ID", ""),
    "instax":    os.environ.get("INSTAX_META_PAGE_ID", ""),
    "m2":        os.environ.get("M2_META_PAGE_ID", ""),
    "pato":      os.environ.get("PATO_META_PAGE_ID", ""),
}

# Polskie miasta → Facebook Location IDs
POLISH_CITIES_FB_IDS = {
    "warszawa":      {"key": "2430536", "name": "Warsaw",        "country": "PL"},
    "warsaw":        {"key": "2430536", "name": "Warsaw",        "country": "PL"},
    "kraków":        {"key": "2520876", "name": "Kraków",        "country": "PL"},
    "krakow":        {"key": "2520876", "name": "Kraków",        "country": "PL"},
    "wrocław":       {"key": "2520930", "name": "Wrocław",       "country": "PL"},
    "wroclaw":       {"key": "2520930", "name": "Wrocław",       "country": "PL"},
    "poznań":        {"key": "2520729", "name": "Poznań",        "country": "PL"},
    "poznan":        {"key": "2520729", "name": "Poznań",        "country": "PL"},
    "gdańsk":        {"key": "2520657", "name": "Gdańsk",        "country": "PL"},
    "gdansk":        {"key": "2520657", "name": "Gdańsk",        "country": "PL"},
    "łódź":          {"key": "2520694", "name": "Łódź",          "country": "PL"},
    "lodz":          {"key": "2520694", "name": "Łódź",          "country": "PL"},
    "katowice":      {"key": "2520684", "name": "Katowice",      "country": "PL"},
    "szczecin":      {"key": "2520744", "name": "Szczecin",      "country": "PL"},
    "bydgoszcz":     {"key": "2520626", "name": "Bydgoszcz",     "country": "PL"},
    "lublin":        {"key": "2520695", "name": "Lublin",        "country": "PL"},
    "białystok":     {"key": "2520607", "name": "Białystok",     "country": "PL"},
    "bialystok":     {"key": "2520607", "name": "Białystok",     "country": "PL"},
    "gdynia":        {"key": "2520660", "name": "Gdynia",        "country": "PL"},
    "częstochowa":   {"key": "2520639", "name": "Częstochowa",   "country": "PL"},
    "czestochowa":   {"key": "2520639", "name": "Częstochowa",   "country": "PL"},
    "rzeszów":       {"key": "2520738", "name": "Rzeszów",       "country": "PL"},
    "rzeszow":       {"key": "2520738", "name": "Rzeszów",       "country": "PL"},
    "toruń":         {"key": "2520756", "name": "Toruń",         "country": "PL"},
    "torun":         {"key": "2520756", "name": "Toruń",         "country": "PL"},
    "sosnowiec":     {"key": "2520746", "name": "Sosnowiec",     "country": "PL"},
    "kielce":        {"key": "2520686", "name": "Kielce",        "country": "PL"},
    "radom":         {"key": "2520733", "name": "Radom",         "country": "PL"},
    "gliwice":       {"key": "2520662", "name": "Gliwice",       "country": "PL"},
    "zabrze":        {"key": "2520769", "name": "Zabrze",        "country": "PL"},
    "olsztyn":       {"key": "2520718", "name": "Olsztyn",       "country": "PL"},
    "bielsko-biała": {"key": "2520610", "name": "Bielsko-Biała", "country": "PL"},
    "bielsko-biala": {"key": "2520610", "name": "Bielsko-Biała", "country": "PL"},
    "opole":         {"key": "2520720", "name": "Opole",         "country": "PL"},
    "zielona góra":  {"key": "2520773", "name": "Zielona Góra",  "country": "PL"},
    "zielona gora":  {"key": "2520773", "name": "Zielona Góra",  "country": "PL"},
    "trójmiasto":    {"key": "2520657", "name": "Gdańsk",        "country": "PL"},  # fallback do Gdańska
}

# Przyjazne nazwy celów kampanii
OBJECTIVE_FRIENDLY = {
    # Nowe nazwy Meta API v19+
    "OUTCOME_TRAFFIC":       "🔗 Ruch na stronę",
    "OUTCOME_ENGAGEMENT":    "💬 Zaangażowanie",
    "OUTCOME_LEADS":         "📋 Pozyskiwanie leadów",
    "OUTCOME_SALES":         "🎯 Sprzedaż / konwersje",
    "OUTCOME_AWARENESS":     "🌟 Świadomość marki",
    "OUTCOME_APP_PROMOTION": "📱 Promocja aplikacji",
    # Nadal akceptowane przez API
    "CONVERSIONS":      "🎯 Konwersje",
    "REACH":            "👥 Zasięg",
    "BRAND_AWARENESS":  "🌟 Świadomość marki",
    "LEAD_GENERATION":  "📋 Pozyskiwanie leadów",
    "VIDEO_VIEWS":      "▶️ Wyświetlenia video",
    "POST_ENGAGEMENT":  "💬 Zaangażowanie (post)",
    "LINK_CLICKS":      "🔗 Kliknięcia w link",
    # Legacy
    "TRAFFIC":          "🔗 Ruch na stronę",
    "ENGAGEMENT":       "💬 Zaangażowanie",
    "APP_INSTALLS":     "📱 Instalacje aplikacji",
}
