"""Tests for Google Ads campaign creation helpers."""
import sys, os, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock, patch

# Load the real module (bypassing the conftest stub) for pure-function tests
_GOOGLE_ADS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "google_ads.py")
_spec = importlib.util.spec_from_file_location("tools.google_ads_real", _GOOGLE_ADS_PATH)
_google_ads_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_google_ads_real)


# ── _parse_budget_micros ───────────────────────────────────────────────────────

class TestParseBudgetMicros:
    def _fn(self):
        return _google_ads_real._parse_budget_micros

    def test_plain_number(self):
        assert self._fn()("50") == 50_000_000

    def test_with_currency(self):
        assert self._fn()("50 PLN") == 50_000_000

    def test_with_zl(self):
        assert self._fn()("100 zł") == 100_000_000

    def test_decimal(self):
        assert self._fn()("6.25") == 6_250_000

    def test_default_empty(self):
        assert self._fn()("") == 10_000_000

    def test_default_none(self):
        assert self._fn()(None) == 10_000_000

    def test_min_clamp(self):
        assert self._fn()("0") == 1_000_000  # min 1 PLN

    def test_max_clamp(self):
        assert self._fn()("9999") == 2_000_000_000  # max 2000 PLN


# ── _map_campaign_type ─────────────────────────────────────────────────────────

class TestMapCampaignType:
    def _fn(self):
        return _google_ads_real._map_campaign_type

    @pytest.mark.parametrize("inp,expected", [
        ("Search", "SEARCH"),
        ("search", "SEARCH"),
        ("Performance Max", "PERFORMANCE_MAX"),
        ("pmax", "PERFORMANCE_MAX"),
        ("YouTube", "VIDEO"),
        ("yt", "VIDEO"),
        ("Display", "DISPLAY"),
        ("Demand Gen", "DEMAND_GEN"),
        ("Shopping", "SHOPPING"),
        ("unknown_type", "SEARCH"),  # default
    ])
    def test_mapping(self, inp, expected):
        assert self._fn()(inp) == expected


# ── _detect_google_client ──────────────────────────────────────────────────────

class TestDetectGoogleClient:
    def _fn(self):
        return _google_ads_real._detect_google_client

    def _accounts(self):
        return {"dre": "1234567890", "instax": "9876543210"}

    def test_detect_by_brand_name(self):
        params = {"brand_name": "DRE", "campaign_name": "Kampania"}
        assert self._fn()(params, self._accounts()) == "dre"

    def test_detect_by_campaign_name(self):
        params = {"campaign_name": "DRE - Search 2026"}
        assert self._fn()(params, self._accounts()) == "dre"

    def test_detect_by_url(self):
        params = {"website_url": "https://dre.eu/drzwi"}
        # "dre" is in "dre.eu" → should detect
        assert self._fn()(params, self._accounts()) == "dre"

    def test_exclude_patoagencja_url(self):
        params = {"website_url": "https://patoagencja.com/dre-kampania"}
        # Should NOT detect "dre" from patoagencja URL
        result = self._fn()(params, self._accounts())
        assert result is None

    def test_returns_none_when_no_match(self):
        params = {"brand_name": "Nieznana Firma", "campaign_name": "Jakaś kampania"}
        assert self._fn()(params, self._accounts()) is None

    def test_detects_instax(self):
        params = {"brand_name": "Instax", "campaign_name": "Instax Kampania"}
        assert self._fn()(params, self._accounts()) == "instax"


# ── _set_bidding_strategy ──────────────────────────────────────────────────────

class TestSetBiddingStrategy:
    def _fn(self):
        return _google_ads_real._set_bidding_strategy

    def _mock_campaign(self):
        """Return a mock campaign with maximize_clicks sub-object."""
        campaign = MagicMock()
        campaign.maximize_clicks = MagicMock()
        campaign.maximize_clicks.target_spend_micros = 0
        campaign.maximize_conversions = MagicMock()
        campaign.maximize_conversions.target_spend_micros = 0
        campaign.manual_cpc = MagicMock()
        campaign.target_cpa = MagicMock()
        campaign.target_roas = MagicMock()
        return campaign

    def test_default_maximize_clicks(self):
        campaign = self._mock_campaign()
        result = self._fn()(campaign, {})
        assert result == "MAXIMIZE_CLICKS"

    def test_maximize_conversions(self):
        campaign = self._mock_campaign()
        result = self._fn()(campaign, {"bidding_strategy": "MAXIMIZE_CONVERSIONS"})
        assert result == "MAXIMIZE_CONVERSIONS"

    def test_manual_cpc(self):
        campaign = self._mock_campaign()
        result = self._fn()(campaign, {"bidding_strategy": "MANUAL_CPC"})
        assert result == "MANUAL_CPC"

    def test_target_cpa_with_value(self):
        campaign = self._mock_campaign()
        result = self._fn()(campaign, {"bidding_strategy": "TARGET_CPA", "target_cpa": "50"})
        assert result == "TARGET_CPA"
        campaign.target_cpa.target_cpa_micros == 50_000_000

    def test_target_cpa_without_value_falls_back(self):
        campaign = self._mock_campaign()
        result = self._fn()(campaign, {"bidding_strategy": "TARGET_CPA", "target_cpa": ""})
        assert result == "MAXIMIZE_CLICKS"  # fallback

    def test_target_roas_with_value(self):
        campaign = self._mock_campaign()
        result = self._fn()(campaign, {"bidding_strategy": "TARGET_ROAS", "target_roas": "300"})
        assert result == "TARGET_ROAS"


# ── generate_google_campaign_preview ──────────────────────────────────────────

class TestGenerateGoogleCampaignPreview:
    def _fn(self):
        return _google_ads_real.generate_google_campaign_preview

    def test_basic_search_preview(self):
        params = {
            "campaign_name": "DRE Search Test",
            "campaign_type": "Search",
            "daily_budget": "50",
            "bidding_strategy": "MAXIMIZE_CLICKS",
            "country": "Polska",
            "locations": [],
            "landing_page_url": "https://dre.eu",
            "keywords": ["drzwi premium", "drzwi drewniane"],
            "ads": {
                "headlines": ["Drzwi DRE", "Premium Drzwi", "Drzwi do Domu"],
                "descriptions": ["Najlepsze drzwi premium."],
            },
        }
        draft = {
            "campaign_id": "123456789",
            "channel_type": "SEARCH",
            "applied_strategy": "MAXIMIZE_CLICKS",
            "keyword_count": 2,
            "ad_resource": "customers/1/adGroupAds/1",
        }
        preview = self._fn()(params, draft)
        assert "DRE Search Test" in preview
        assert "Search" in preview
        assert "50" in preview
        assert "123456789" in preview
        assert "wstrzymana" in preview.lower() or "WSTRZYMANA" in preview

    def test_preview_contains_keywords(self):
        params = {
            "campaign_name": "Test",
            "campaign_type": "Search",
            "daily_budget": "20",
            "keywords": ["słowo1", "słowo2", "słowo3"],
            "ads": {"headlines": [], "descriptions": []},
        }
        draft = {"campaign_id": "999", "channel_type": "SEARCH",
                 "applied_strategy": "MAXIMIZE_CLICKS", "keyword_count": 3}
        preview = self._fn()(params, draft)
        assert "słowo1" in preview

    def test_preview_pmax_type(self):
        params = {
            "campaign_name": "PMax Test",
            "campaign_type": "Performance Max",
            "daily_budget": "100",
            "country": "Polska",
            "ads": {"headlines": [], "descriptions": []},
        }
        draft = {"campaign_id": "555", "channel_type": "PERFORMANCE_MAX",
                 "applied_strategy": "MAXIMIZE_CONVERSIONS", "keyword_count": 0}
        preview = self._fn()(params, draft)
        assert "Performance Max" in preview


# ── Integration: wizard completion flow ───────────────────────────────────────

class TestGoogleWizardCompletionFlow:
    """Ensure wizard creates draft and shows preview after KAMPANIA_GOOGLE_GOTOWA."""

    def setup_method(self):
        import _ctx
        _ctx.google_campaign_wizard.clear()

    def _make_wizard(self):
        return {
            "messages": [],
            "source_channel": "C123",
            "thread_ts": "111.222",
            "mode": "simple",
            "resolved_mode": "simple",
            "files": [],
        }

    def test_wizard_deleted_after_draft_creation(self):
        import _ctx
        import bot

        _ctx.google_campaign_wizard["U456"] = self._make_wizard()

        completion_text = (
            "===KAMPANIA_GOOGLE_GOTOWA===\n"
            "Gotowe!\n"
            "```json\n"
            '{"campaign_name":"DRE Search Test","campaign_type":"Search",'
            '"brand_name":"DRE","daily_budget":"50","country":"Polska",'
            '"locations":[],"keywords":["drzwi"],'
            '"ads":{"headlines":["H1","H2","H3"],"descriptions":["Opis"]},'
            '"landing_page_url":"https://dre.eu","ready_to_create":true}\n'
            "```\n"
        )

        say = MagicMock()
        mock_draft = {
            "campaign_id": "GCAM_999",
            "campaign_resource": "customers/1/campaigns/999",
            "budget_resource": "customers/1/campaignBudgets/1",
            "adgroup_resource": "customers/1/adGroups/1",
            "ad_resource": "customers/1/adGroupAds/1",
            "keyword_count": 1,
            "customer_id": "1234567890",
            "applied_strategy": "MAXIMIZE_CLICKS",
            "channel_type": "SEARCH",
            "params": {},
        }

        with patch.dict(os.environ, {"GOOGLE_ADS_CUSTOMER_IDS": '{"dre":"1234567890"}'}), \
             patch.object(bot, "_detect_google_client", return_value="dre"), \
             patch.object(bot, "create_google_campaign_draft", return_value=mock_draft), \
             patch.object(bot, "generate_google_campaign_preview", return_value="📋 Preview Google"):
            with patch.object(_ctx.claude.messages, "create") as mock_claude:
                mock_claude.return_value.content = [MagicMock(text=completion_text)]
                bot._handle_google_campaign_wizard("U456", "potwierdź i utwórz", [], say)

        assert "U456" not in _ctx.google_campaign_wizard, "Wizard should be deleted after draft"
        say.assert_any_call("📋 Preview Google")

    def test_wizard_handles_missing_customer_id(self):
        import _ctx
        import bot

        _ctx.google_campaign_wizard["U789"] = self._make_wizard()

        completion_text = (
            "===KAMPANIA_GOOGLE_GOTOWA===\n"
            "```json\n"
            '{"campaign_name":"Nieznany Klient","campaign_type":"Search",'
            '"brand_name":"XYZ Corp","daily_budget":"10","ready_to_create":true}\n'
            "```\n"
        )

        say = MagicMock()
        with patch.dict(os.environ, {"GOOGLE_ADS_CUSTOMER_IDS": '{"dre":"1234567890"}'}):
            with patch.object(_ctx.claude.messages, "create") as mock_claude:
                mock_claude.return_value.content = [MagicMock(text=completion_text)]
                bot._handle_google_campaign_wizard("U789", "gotowe", [], say)

        assert "U789" not in _ctx.google_campaign_wizard
        # Should have shown error about missing account
        calls_text = " ".join(str(c) for c in say.call_args_list)
        assert "konto" in calls_text.lower() or "customer" in calls_text.lower() or "klienta" in calls_text.lower()
