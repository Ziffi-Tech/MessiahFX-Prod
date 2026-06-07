"""
Venue identifiers and symbol→venue routing.

Strategies scan a configured list of symbols and must decide which venue each
trades on. Historically this was a hard-coded `"binance" if "USDT" in symbol else
"oanda"`, which made it impossible to route a symbol to Bybit/OKX/etc.

A configured entry may now carry an explicit `<venue>:<symbol>` prefix, e.g.
`bybit:BTC/USDT:USDT`. Because ccxt symbols can themselves contain ':' (linear
perps are `BASE/QUOTE:QUOTE`), the prefix is only honoured when the segment
before the first ':' is a KNOWN venue — otherwise the venue is inferred from the
symbol's shape, preserving the old behaviour for plain entries.
"""

from __future__ import annotations

# Venues the platform can route orders to. "paper" is the simulated venue.
KNOWN_VENUES: frozenset[str] = frozenset({
    "binance", "bybit", "okx", "kraken", "coinbase", "oanda", "mt5", "paper",
})

# Quote currencies that mark a symbol as crypto (used only for shape inference).
_CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "BTC", "ETH", "BNB", "USD")


def infer_venue(symbol: str) -> str:
    """
    Best-effort venue from a symbol's shape when no explicit prefix is given.

    Forex internal format (EUR_USD) → oanda; crypto pairs (BTC/USDT) → binance.
    This only sets a default; use an explicit `<venue>:` prefix to override.
    """
    s = symbol.strip().upper()
    if "_" in s:                      # forex internal format, e.g. EUR_USD
        return "oanda"
    if "/" in s:                      # crypto pair, e.g. BTC/USDT or BTC/USDT:USDT
        return "binance"
    if any(s.endswith(q) for q in _CRYPTO_QUOTES):  # bare crypto, e.g. BTCUSDT
        return "binance"
    return "binance"


def parse_symbol_spec(spec: str) -> tuple[str, str]:
    """
    Parse a configured symbol spec into (venue, symbol).

    Examples:
        "BTC/USDT"            -> ("binance", "BTC/USDT")
        "EUR_USD"             -> ("oanda",   "EUR_USD")
        "bybit:BTC/USDT:USDT" -> ("bybit",   "BTC/USDT:USDT")
        "okx:ETH/USDT:USDT"   -> ("okx",     "ETH/USDT:USDT")
        "oanda:EUR_USD"       -> ("oanda",   "EUR_USD")
    """
    spec = spec.strip()
    if ":" in spec:
        head, rest = spec.split(":", 1)
        if head.lower() in KNOWN_VENUES:
            return head.lower(), rest.strip()
    return infer_venue(spec), spec
