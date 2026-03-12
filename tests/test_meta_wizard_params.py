"""
Tests for _meta_wizard_json_to_params — the function that converts
Claude's wizard JSON into create_campaign_draft params.

These are the exact bugs that kept breaking campaigns with wrong data.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime


# ── Import the function under test ────────────────────────────────────────────

def get_fn():
    """Import lazily so conftest stubs are in place first."""
    import importlib, bot
    importlib.reload(bot) if hasattr(bot, "_reloaded") else None
    return bot._meta_wizard_json_to_params


# ── Fixtures ──────────────────────────────────────────────────────────────────

SIMPLE_JSON = {
    "mode": "simple",
    "campaign_name": "DRZWI DRE SUPER",
    "objective": "traffic",
    "daily_budget": "6.25",
    "country": "Polska",
    "age_range": "18-30",
    "gender": "female",
    "interests": ["wnętrza", "design"],
    "landing_page_url": "https://dre.eu",
    "creative": {
        "type": "image",
        "primary_text": "ELO 320",
        "headline": "Kup teraz",
        "cta": "LEARN_MORE",
    },
    "ready_to_create": True,
}

PRO_JSON = {
    "mode": "pro",
    "campaign_name": "DRE Kampania Wiosna 2026",
    "objective": "OUTCOME_LEADS",
    "budget_daily": "50",
    "location": ["Warszawa", "Kraków"],
    "age_range": "25-54",
    "gender": "all",
    "audiences": [
        {"interests": ["interior design", "home decor"]},
    ],
    "landing_page_url": "https://dre.eu/kontakt",
    "creative": {
        "primary_text": "Sprawdź nasze drzwi",
        "headlines": ["Drzwi Premium", "Zamów teraz"],
        "cta": "GET_QUOTE",
    },
    "schedule": {"start_date": "2026-04-01", "end_date": "2026-04-30"},
    "ready_to_create": True,
}


# ── client_name detection ─────────────────────────────────────────────────────

class TestClientNameDetection:
    def test_dre_from_campaign_name(self):
        fn = get_fn()
        p = fn({"mode": "simple", "campaign_name": "DRZWI DRE kampania", "landing_page_url": ""}, {})
        assert p["client_name"] == "dre"

    def test_dre_from_url(self):
        fn = get_fn()
        p = fn({"mode": "simple", "campaign_name": "Nowa kampania", "landing_page_url": "https://dre.eu"}, {})
        assert p["client_name"] == "dre"

    def test_instax_detected(self):
        fn = get_fn()
        p = fn({"mode": "simple", "campaign_name": "Instax kampania", "landing_page_url": ""}, {})
        assert p["client_name"] == "instax"

    def test_m2_detected(self):
        fn = get_fn()
        p = fn({"mode": "simple", "campaign_name": "m2 nieruchomości", "landing_page_url": ""}, {})
        assert p["client_name"] == "m2"

    def test_unknown_defaults_to_dre(self):
        fn = get_fn()
        p = fn({"mode": "simple", "campaign_name": "Nieznany klient", "landing_page_url": ""}, {})
        assert p["client_name"] == "dre"


# ── Budget parsing ─────────────────────────────────────────────────────────────

class TestBudget:
    def test_float_string(self):
        fn = get_fn()
        p = fn({**SIMPLE_JSON, "daily_budget": "6.25"}, {})
        assert p["daily_budget"] == 6.25

    def test_int_string(self):
        fn = get_fn()
        p = fn({**SIMPLE_JSON, "daily_budget": "50"}, {})
        assert p["daily_budget"] == 50.0

    def test_pln_suffix(self):
        fn = get_fn()
        p = fn({**SIMPLE_JSON, "daily_budget": "25 PLN"}, {})
        assert p["daily_budget"] == 25.0

    def test_pro_budget_daily_key(self):
        fn = get_fn()
        p = fn({**PRO_JSON}, {})
        assert p["daily_budget"] == 50.0

    def test_missing_budget_defaults(self):
        fn = get_fn()
        j = {k: v for k, v in SIMPLE_JSON.items() if k != "daily_budget"}
        p = fn(j, {})
        assert p["daily_budget"] == 10.0


# ── Objective mapping ──────────────────────────────────────────────────────────

class TestObjective:
    @pytest.mark.parametrize("raw,expected", [
        ("traffic",           "OUTCOME_TRAFFIC"),
        ("TRAFFIC",           "OUTCOME_TRAFFIC"),
        ("OUTCOME_TRAFFIC",   "OUTCOME_TRAFFIC"),
        ("leads",             "OUTCOME_LEADS"),
        ("OUTCOME_LEADS",     "OUTCOME_LEADS"),
        ("sales",             "OUTCOME_SALES"),
        ("engagement",        "OUTCOME_ENGAGEMENT"),
        ("awareness",         "OUTCOME_AWARENESS"),
        ("messages",          "OUTCOME_ENGAGEMENT"),
        ("VIDEO_VIEWS",       "OUTCOME_ENGAGEMENT"),  # was crashing before
        ("GIBBERISH",         "OUTCOME_TRAFFIC"),     # unknown → safe fallback
    ])
    def test_objective(self, raw, expected):
        fn = get_fn()
        p = fn({**SIMPLE_JSON, "objective": raw}, {})
        assert p["objective"] == expected


# ── Gender mapping (CRITICAL — was passing genders:[2] instead of gender:"female") ──

class TestGender:
    @pytest.mark.parametrize("raw,expected", [
        ("female",    "female"),
        ("Female",    "female"),
        ("kobiety",   "female"),
        ("kobieta",   "female"),
        ("women",     "female"),
        ("male",      "male"),
        ("mężczyźni", "male"),
        ("mezczyzni", "male"),
        ("men",       "male"),
        ("all",       "all"),
        ("wszyscy",   "all"),
        ("both",      "all"),
        ("",          "all"),
    ])
    def test_gender_string(self, raw, expected):
        fn = get_fn()
        p = fn({**SIMPLE_JSON, "gender": raw}, {})
        # CRITICAL: must be string key "gender", NOT "genders" (list)
        assert "gender" in p["targeting"], "targeting must have 'gender' key (not 'genders')"
        assert "genders" not in p["targeting"], "targeting must NOT have 'genders' key"
        assert p["targeting"]["gender"] == expected


# ── Age range ─────────────────────────────────────────────────────────────────

class TestAgeRange:
    def test_standard(self):
        fn = get_fn()
        p = fn({**SIMPLE_JSON, "age_range": "18-30"}, {})
        assert p["targeting"]["age_min"] == 18
        assert p["targeting"]["age_max"] == 30

    def test_wider_range(self):
        fn = get_fn()
        p = fn({**SIMPLE_JSON, "age_range": "25-54"}, {})
        assert p["targeting"]["age_min"] == 25
        assert p["targeting"]["age_max"] == 54

    def test_missing_defaults(self):
        fn = get_fn()
        j = {k: v for k, v in SIMPLE_JSON.items() if k != "age_range"}
        p = fn(j, {})
        assert p["targeting"]["age_min"] == 18
        assert p["targeting"]["age_max"] == 65


# ── Location ──────────────────────────────────────────────────────────────────

class TestLocation:
    def test_simple_country(self):
        fn = get_fn()
        p = fn({**SIMPLE_JSON, "country": "Polska"}, {})
        assert "Polska" in p["targeting"]["locations"]

    def test_pro_location_list(self):
        fn = get_fn()
        p = fn({**PRO_JSON}, {})
        assert "Warszawa" in p["targeting"]["locations"]
        assert "Kraków" in p["targeting"]["locations"]

    def test_pro_location_string(self):
        fn = get_fn()
        p = fn({**PRO_JSON, "location": "Warszawa"}, {})
        assert "Warszawa" in p["targeting"]["locations"]


# ── Interests ─────────────────────────────────────────────────────────────────

class TestInterests:
    def test_simple_interests(self):
        fn = get_fn()
        p = fn({**SIMPLE_JSON, "interests": ["wnętrza", "design"]}, {})
        assert "wnętrza" in p["targeting"]["interests"]
        assert "design" in p["targeting"]["interests"]

    def test_pro_interests_from_audiences(self):
        fn = get_fn()
        p = fn({**PRO_JSON}, {})
        assert "interior design" in p["targeting"]["interests"]
        assert "home decor" in p["targeting"]["interests"]


# ── CTA normalization ─────────────────────────────────────────────────────────

class TestCTA:
    @pytest.mark.parametrize("raw,expected", [
        ("LEARN_MORE",         "LEARN_MORE"),
        ("learn_more",         "LEARN_MORE"),
        ("SHOP_NOW",           "SHOP_NOW"),
        ("Odwiedź stronę",     "LEARN_MORE"),
        ("ODWIEDŹ_STRONĘ",     "LEARN_MORE"),
        ("Dowiedz się więcej", "LEARN_MORE"),
        ("KUP_TERAZ",          "SHOP_NOW"),
        ("GET_QUOTE",          "GET_QUOTE"),
        ("GARBAGE_VALUE",      "LEARN_MORE"),  # unknown → safe fallback
    ])
    def test_cta(self, raw, expected):
        fn = get_fn()
        j = {**SIMPLE_JSON}
        j["creative"] = {**SIMPLE_JSON["creative"], "cta": raw}
        p = fn(j, {})
        assert p["call_to_action"] == expected


# ── Date normalization ────────────────────────────────────────────────────────

class TestDates:
    def test_iso_date_passthrough(self):
        fn = get_fn()
        p = fn({**PRO_JSON, "schedule": {"start_date": "2026-04-01", "end_date": "2026-04-30"}}, {})
        assert p["start_date"] == "2026-04-01"
        assert p["end_date"] == "2026-04-30"

    def test_dot_format_normalized(self):
        fn = get_fn()
        p = fn({**PRO_JSON, "schedule": {"start_date": "01.04.2026", "end_date": "30.04.2026"}}, {})
        assert p["start_date"] == "2026-04-01"
        assert p["end_date"] == "2026-04-30"

    def test_no_end_date_is_none(self):
        fn = get_fn()
        p = fn({**SIMPLE_JSON}, {})
        assert p["end_date"] is None

    def test_missing_start_defaults_to_today(self):
        fn = get_fn()
        p = fn({**SIMPLE_JSON}, {})
        assert p["start_date"] == datetime.now().strftime("%Y-%m-%d")


# ── Required output keys ──────────────────────────────────────────────────────

class TestRequiredKeys:
    def test_all_required_keys_present(self):
        fn = get_fn()
        p = fn(SIMPLE_JSON, {})
        for key in ("client_name", "campaign_name", "objective", "daily_budget",
                    "website_url", "ad_copy", "call_to_action", "start_date",
                    "targeting"):
            assert key in p, f"Missing required key: {key}"
        for tkey in ("gender", "age_min", "age_max", "locations", "interests"):
            assert tkey in p["targeting"], f"Missing targeting key: {tkey}"
