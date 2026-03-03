"""
All project-wide constants for Sebol bot.
No imports from other project modules — safe to import from anywhere.
"""
import os

# Base directory = slack-bot/  (one level up from config/)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── FILE PATHS ─────────────────────────────────────────────────────────────────
AVAILABILITY_FILE     = os.path.join(_BASE_DIR, "data", "team_availability.json")
REQUESTS_FILE         = os.path.join(_BASE_DIR, "data", "team_requests.json")
ONBOARDING_FILE       = os.path.join(_BASE_DIR, "data", "onboardings.json")
STANDUP_FILE          = os.path.join(_BASE_DIR, "data", "standup.json")
HISTORY_FILE          = os.path.join(_BASE_DIR, "data", "campaign_history.json")
_DIGEST_LAST_SENT_FILE = os.path.join(_BASE_DIR, "data", "digest_last_sent.json")

HISTORY_RETENTION_DAYS = 90
_DIGEST_INTERVAL_DAYS  = 3


# ── STANDUP ────────────────────────────────────────────────────────────────────
STANDUP_CHANNEL = os.environ.get("STANDUP_CHANNEL_ID",
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
