"""
Tests for build_meta_targeting — the function that converts
user-friendly targeting dict → Meta Ads API format.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock


def get_fn():
    from tools.campaign_creator import build_meta_targeting
    return build_meta_targeting


class TestGender:
    def test_female(self):
        r = get_fn()({"gender": "female", "age_min": 18, "age_max": 65, "locations": ["Polska"]})
        assert r["genders"] == [2]

    def test_male(self):
        r = get_fn()({"gender": "male", "age_min": 18, "age_max": 65, "locations": ["Polska"]})
        assert r["genders"] == [1]

    def test_all_no_genders_key(self):
        r = get_fn()({"gender": "all", "age_min": 18, "age_max": 65, "locations": ["Polska"]})
        assert "genders" not in r

    def test_missing_gender_defaults_to_all(self):
        r = get_fn()({"age_min": 18, "age_max": 65, "locations": ["Polska"]})
        assert "genders" not in r


class TestAge:
    def test_custom_age(self):
        r = get_fn()({"age_min": 25, "age_max": 45, "locations": ["Polska"]})
        assert r["age_min"] == 25
        assert r["age_max"] == 45

    def test_defaults(self):
        r = get_fn()({"locations": ["Polska"]})
        assert r["age_min"] == 18
        assert r["age_max"] == 65


class TestLocations:
    def test_poland(self):
        r = get_fn()({"locations": ["Polska"]})
        assert r["geo_locations"]["countries"] == ["PL"]

    def test_warsaw_city(self):
        r = get_fn()({"locations": ["Warszawa"]})
        assert "cities" in r["geo_locations"]
        assert r["geo_locations"]["cities"][0]["name"] == "Warsaw"

    def test_krakow_with_polish_chars(self):
        r = get_fn()({"locations": ["Kraków"]})
        assert "cities" in r["geo_locations"]

    def test_empty_defaults_to_pl(self):
        r = get_fn()({"locations": []})
        assert r["geo_locations"]["countries"] == ["PL"]

    def test_missing_locations_defaults_to_pl(self):
        r = get_fn()({})
        assert r["geo_locations"]["countries"] == ["PL"]


class TestInterests:
    def test_no_interests_no_flexible_spec(self):
        r = get_fn()({"locations": ["Polska"], "interests": []})
        assert "flexible_spec" not in r

    @patch("tools.campaign_creator.search_meta_interests")
    def test_interests_searched_and_mapped(self, mock_search):
        mock_search.return_value = [{"id": "123", "name": "Interior Design"}]
        r = get_fn()({"locations": ["Polska"], "interests": ["interior design"]})
        assert "flexible_spec" in r
        assert r["flexible_spec"][0]["interests"][0]["id"] == "123"

    @patch("tools.campaign_creator.search_meta_interests")
    def test_interest_not_found_skipped(self, mock_search):
        mock_search.return_value = []
        r = get_fn()({"locations": ["Polska"], "interests": ["noneexistent_xyz"]})
        assert "flexible_spec" not in r


class TestIntegration:
    """Full targeting dict as returned by _meta_wizard_json_to_params."""

    def test_wizard_output_compatible(self):
        """Simulate exact output from _meta_wizard_json_to_params."""
        targeting_from_wizard = {
            "gender": "female",   # string — NOT genders:[2]
            "age_min": 18,
            "age_max": 30,
            "interests": [],
            "locations": ["Polska"],
        }
        r = get_fn()(targeting_from_wizard)
        assert r["genders"] == [2]
        assert r["age_min"] == 18
        assert r["age_max"] == 30
        assert r["geo_locations"]["countries"] == ["PL"]
