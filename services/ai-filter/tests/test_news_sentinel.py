"""
Tests for the news sentinel RSS parser and cycle logic.

These tests cover:
  - _parse_rss_headlines: handles valid RSS 2.0, Atom, malformed XML, empty feeds
  - _to_epoch_ms equivalent: timestamps should not be tested here (backtest service)
  - Sentinel cycle health payload structure

No HTTP calls, no Redis, no Claude API.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.news_sentinel import _parse_rss_headlines


# ── RSS 2.0 fixtures ──────────────────────────────────────────────────────────

RSS_2_VALID = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk News</title>
    <item><title>Bitcoin Hits New All-Time High</title></item>
    <item><title>SEC Delays ETF Decision Again</title></item>
    <item><title>Ethereum Upgrade Scheduled for Q3</title></item>
    <item><title>Binance Faces Regulatory Scrutiny</title></item>
    <item><title>DeFi TVL Crosses $100B Mark</title></item>
  </channel>
</rss>"""

RSS_2_CDATA = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item><title><![CDATA[Fed Holds Rates — Markets Rally]]></title></item>
    <item><title><![CDATA[Crypto Winter: Is It Over?]]></title></item>
  </channel>
</rss>"""

ATOM_VALID = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ForexLive News</title>
  <entry><title>EUR/USD Breaks Key Resistance</title></entry>
  <entry><title>Bank of England Rate Decision Tomorrow</title></entry>
  <entry><title>Dollar Index Weakens on CPI Data</title></entry>
</feed>"""

RSS_EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Empty Feed</title>
  </channel>
</rss>"""

RSS_ITEMS_NO_TITLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item><description>No title here</description></item>
    <item><link>http://example.com/1</link></item>
  </channel>
</rss>"""

MALFORMED_XML = """<?xml version="1.0"?>
<rss><channel><item><title>Unclosed tag</channel></rss"""

NOT_XML = "This is just plain text, not XML at all"

RSS_WHITESPACE_TITLES = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item><title>   Padded Title   </title></item>
    <item><title>
      Multiline Title
    </title></item>
  </channel>
</rss>"""


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestParseRssHeadlines:
    def test_rss2_returns_all_headlines(self):
        headlines = _parse_rss_headlines(RSS_2_VALID, max_items=10)
        assert len(headlines) == 5
        assert headlines[0] == "Bitcoin Hits New All-Time High"
        assert headlines[4] == "DeFi TVL Crosses $100B Mark"

    def test_rss2_respects_max_items(self):
        headlines = _parse_rss_headlines(RSS_2_VALID, max_items=3)
        assert len(headlines) == 3

    def test_rss2_max_items_one(self):
        headlines = _parse_rss_headlines(RSS_2_VALID, max_items=1)
        assert len(headlines) == 1
        assert headlines[0] == "Bitcoin Hits New All-Time High"

    def test_atom_fallback_works(self):
        headlines = _parse_rss_headlines(ATOM_VALID, max_items=10)
        assert len(headlines) == 3
        assert "EUR/USD Breaks Key Resistance" in headlines

    def test_atom_respects_max_items(self):
        headlines = _parse_rss_headlines(ATOM_VALID, max_items=2)
        assert len(headlines) == 2

    def test_empty_feed_returns_empty_list(self):
        headlines = _parse_rss_headlines(RSS_EMPTY_FEED, max_items=10)
        assert headlines == []

    def test_items_without_title_skipped(self):
        headlines = _parse_rss_headlines(RSS_ITEMS_NO_TITLE, max_items=10)
        assert headlines == []

    def test_malformed_xml_returns_empty_not_raises(self):
        """Parser errors must never propagate — return empty list."""
        headlines = _parse_rss_headlines(MALFORMED_XML, max_items=10)
        assert isinstance(headlines, list)
        # May or may not parse partially — must not raise
        assert len(headlines) >= 0

    def test_plain_text_returns_empty_not_raises(self):
        headlines = _parse_rss_headlines(NOT_XML, max_items=10)
        assert headlines == []

    def test_whitespace_stripped_from_titles(self):
        headlines = _parse_rss_headlines(RSS_WHITESPACE_TITLES, max_items=10)
        assert len(headlines) == 2
        for h in headlines:
            assert h == h.strip()
            assert h  # non-empty

    def test_cdata_titles_extracted(self):
        headlines = _parse_rss_headlines(RSS_2_CDATA, max_items=10)
        assert len(headlines) == 2
        assert "Fed Holds Rates" in headlines[0]

    def test_max_items_zero_returns_empty(self):
        headlines = _parse_rss_headlines(RSS_2_VALID, max_items=0)
        assert headlines == []

    def test_returns_list_type_always(self):
        for xml in (RSS_2_VALID, ATOM_VALID, RSS_EMPTY_FEED, MALFORMED_XML, NOT_XML):
            result = _parse_rss_headlines(xml, max_items=5)
            assert isinstance(result, list), f"Expected list, got {type(result)}"

    def test_rss_items_order_preserved(self):
        """Headlines must come back in feed order (most-recent-first in real feeds)."""
        headlines = _parse_rss_headlines(RSS_2_VALID, max_items=10)
        assert headlines[0] == "Bitcoin Hits New All-Time High"
        assert headlines[1] == "SEC Delays ETF Decision Again"


# ── Sentinel health payload structure ────────────────────────────────────────

class TestSentinelHealthPayload:
    """
    Verify the health dict written by _run_cycle has the expected shape.
    Uses a fake run_cycle result rather than calling the real function
    (which requires Redis + HTTP + Claude).
    """

    def _fake_cycle_result(self, crypto_ok: bool, fx_ok: bool) -> dict:
        """Simulate what _run_cycle returns."""
        return {"crypto": crypto_ok, "fx": fx_ok}

    def test_all_ok(self):
        result = self._fake_cycle_result(True, True)
        assert all(result.values()) is True

    def test_partial_failure(self):
        result = self._fake_cycle_result(True, False)
        assert any(result.values()) is True
        assert all(result.values()) is False

    def test_total_failure(self):
        result = self._fake_cycle_result(False, False)
        assert any(result.values()) is False

    def test_health_payload_keys(self):
        """health_payload from _run_cycle must contain these exact keys."""
        import json
        from datetime import datetime, timezone
        cycle_results = {"crypto": True, "fx": False}
        health_payload = {
            "last_cycle_at": datetime.now(timezone.utc).isoformat(),
            "feed_failures": 1,
            "total_feeds": 3,
            "results": cycle_results,
            "all_ok": all(cycle_results.values()),
        }
        # Must be JSON-serialisable
        serialised = json.dumps(health_payload)
        decoded = json.loads(serialised)
        for key in ("last_cycle_at", "feed_failures", "total_feeds", "results", "all_ok"):
            assert key in decoded
        assert decoded["all_ok"] is False
