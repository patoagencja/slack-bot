"""Ticker auto-detection tests — $TICKER preferred, <=3 tickers, no silent drop."""

from investing import universe


def test_cashtags_detected_in_order():
    found = universe.detect_tickers("kupować $NVDA czy $AMD? a może $MU?")
    assert found == ["NVDA", "AMD", "MU"]


def test_caps_at_three():
    found = universe.detect_tickers("$NVDA $AMD $MU $META $MSFT")
    assert len(found) == 3
    overflow = universe.detect_tickers_overflow("$NVDA $AMD $MU $META $MSFT")
    assert "META" in overflow and "MSFT" in overflow


def test_bare_words_only_if_watchlist():
    # NVDA is on the watchlist; RANDOM is not
    found = universe.detect_tickers("co sądzisz o NVDA i jakimś RANDOM tickerze")
    assert "NVDA" in found
    assert "RANDOM" not in found


def test_dedup():
    found = universe.detect_tickers("$NVDA NVDA $NVDA")
    assert found == ["NVDA"]


def test_sector_and_narrative_maps():
    assert universe.sector_of("NVDA") == "AI/Semis"
    assert universe.narrative_of("MSTR") == "Crypto"
    assert universe.sector_benchmark("NVDA") == "SMH"


# ── /wejscie command parsing ──────────────────────────────────────────────────
def test_parse_basic_command():
    p = universe.parse_entry_command("NVDA 50000 risk=0.5")
    assert p["tickers"] == ["NVDA"]
    assert p["amount"] == 50000.0
    assert p["risk"] == 0.5
    assert p["rejected"] == []


def test_parse_company_name_alias():
    # the exact case that failed in Slack: a company name, not a symbol
    p = universe.parse_entry_command("nvidia")
    assert p["tickers"] == ["NVDA"]
    assert p["rejected"] == []


def test_parse_rejects_unknown_long_word_no_garbage_substring():
    p = universe.parse_entry_command("someunknowncompany 100000")
    assert p["tickers"] == []
    assert "someunknowncompany" in p["rejected"]
    assert p["amount"] == 100000.0


def test_parse_multiple_cashtags_capped():
    p = universe.parse_entry_command("$NVDA $AMD $MU $META")
    assert p["tickers"] == ["NVDA", "AMD", "MU"]
    assert p["overflow"] == ["META"]


def test_parse_lowercase_symbol():
    p = universe.parse_entry_command("nvda")
    assert p["tickers"] == ["NVDA"]


def test_parse_defaults_when_absent():
    p = universe.parse_entry_command("NVDA")
    assert p["amount"] is None and p["risk"] is None
