"""
Tests for JSON extraction from Claude's completion message.
Claude sometimes wraps JSON differently — all variants must work.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import re
import json
import pytest


SAMPLE_JSON = {
    "mode": "simple",
    "campaign_name": "DRE test",
    "objective": "traffic",
    "daily_budget": "10",
    "country": "Polska",
    "age_range": "18-65",
    "gender": "all",
    "interests": [],
    "landing_page_url": "https://dre.eu",
    "creative": {"primary_text": "tekst", "cta": "LEARN_MORE"},
    "ready_to_create": True,
}


def extract_json(text: str):
    """Same logic as in bot.py ===KAMPANIA_META_GOTOWA=== handler."""
    for pattern in (
        r"```json\s*(\{[\s\S]*?\})\s*```",
        r"```\s*(\{[\s\S]*?\})\s*```",
        r"(\{[\s\S]*\"mode\"[\s\S]*\})",
    ):
        m = re.search(pattern, text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None


class TestJsonExtraction:
    def _make_text(self, wrapper):
        j = json.dumps(SAMPLE_JSON, ensure_ascii=False, indent=2)
        return wrapper.format(json=j)

    def test_json_code_fence(self):
        text = self._make_text("Oto kampania:\n```json\n{json}\n```\nGotowe!")
        result = extract_json(text)
        assert result is not None
        assert result["campaign_name"] == "DRE test"

    def test_plain_code_fence(self):
        text = self._make_text("Oto kampania:\n```\n{json}\n```\nGotowe!")
        result = extract_json(text)
        assert result is not None
        assert result["mode"] == "simple"

    def test_raw_json_no_fence(self):
        j = json.dumps(SAMPLE_JSON, ensure_ascii=False)
        text = f"Kampania gotowa!\n{j}\nMożemy uruchamiać."
        result = extract_json(text)
        assert result is not None
        assert result["daily_budget"] == "10"

    def test_json_with_nested_objects(self):
        """Ensure nested braces (creative dict) don't break extraction."""
        text = self._make_text("```json\n{json}\n```")
        result = extract_json(text)
        assert result is not None
        assert result["creative"]["cta"] == "LEARN_MORE"

    def test_no_json_returns_none(self):
        text = "Kampania gotowa ale bez JSONa w odpowiedzi."
        result = extract_json(text)
        assert result is None

    def test_malformed_json_skips_gracefully(self):
        text = "```json\n{broken json here\n```"
        result = extract_json(text)
        assert result is None

    def test_marker_stripped_before_parsing(self):
        """===KAMPANIA_META_GOTOWA=== marker must not break JSON extraction."""
        j = json.dumps(SAMPLE_JSON, ensure_ascii=False, indent=2)
        text = f"===KAMPANIA_META_GOTOWA===\nPodsumowanie:\n```json\n{j}\n```"
        result = extract_json(text)
        assert result is not None
        assert result["campaign_name"] == "DRE test"
