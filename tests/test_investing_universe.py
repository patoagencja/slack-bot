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
