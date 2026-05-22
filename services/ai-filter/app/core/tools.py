"""
Agent tool implementations.

Each function corresponds to one tool Claude can call during an agentic loop.
All functions:
  - Accept a typed dict matching the tool's input_schema
  - Return a JSON-serialisable dict
  - Never raise — return {"error": "..."} on failure so Claude can decide what to do
  - Have individual timeouts (10-30s depending on the tool)

Data sources:
  - Journal service (HTTP):      /trades, /pnl/summary, /opportunities
  - Backtest service (HTTP):     /backtest/funding-arb, /backtest/stat-arb
  - RAG service (HTTP):          /query
  - Risk service (HTTP):         /health/state
  - Redis (direct):              tick cache, regime cache, risk state

Tool registry:
  ALL_TOOLS     — list of all Anthropic tool definitions (for schemas)
  execute_tool  — dispatcher: (name, input, ctx) → result dict
"""

import json
from typing import Any

import httpx
import structlog
from redis.asyncio import Redis

from mezna_shared.redis_client import RedisKeys

log = structlog.get_logger()

# ── Tool definitions (Anthropic schema format) ────────────────────────────────

SEARCH_KNOWLEDGE_BASE = {
    "name": "search_knowledge_base",
    "description": (
        "Search MeznaQuantFX's RAG knowledge base for information about trading strategies, "
        "market research, backtesting methodology, or any indexed documents. "
        "Use this first when answering questions about how strategies work or when "
        "you need theoretical/research context."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query (max 500 chars).",
            },
            "category": {
                "type": "string",
                "description": "Optional category filter: strategy_note, market_research, risk_policy, backtest_report",
            },
        },
        "required": ["query"],
    },
}

GET_TRADES = {
    "name": "get_trades",
    "description": (
        "Fetch recent trades from the journal. Returns filled, rejected, and error trades "
        "with full execution details. Use to analyse execution quality, fill rates, "
        "slippage patterns, and strategy-level performance."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "strategy_type": {
                "type": "string",
                "description": "Filter by strategy: funding_arb, stat_arb, swing, tv_signal",
            },
            "status": {
                "type": "string",
                "description": "Filter by status: filled, rejected, error, pending",
            },
            "venue": {
                "type": "string",
                "description": "Filter by venue: binance, oanda, mt5",
            },
            "since_days": {
                "type": "integer",
                "description": "How many days back to fetch (default: 7, max: 90)",
                "minimum": 1,
                "maximum": 90,
            },
            "limit": {
                "type": "integer",
                "description": "Max trades to return (default: 50, max: 100)",
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": [],
    },
}

GET_TRADE_DETAILS = {
    "name": "get_trade_details",
    "description": (
        "Fetch the full details of a single trade plus its linked opportunity "
        "(AI score, risk checks, signal metrics at the time of the trade). "
        "Use when you need to investigate a specific trade in depth."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "client_order_id": {
                "type": "string",
                "description": "The client_order_id of the trade to fetch.",
            },
        },
        "required": ["client_order_id"],
    },
}

GET_PNL_SUMMARY = {
    "name": "get_pnl_summary",
    "description": (
        "Get aggregated P&L and trade statistics for the last N days. "
        "Returns total fills, fill rate, total notional, fees, realized P&L, and net P&L. "
        "Use to assess overall system performance over a period."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of calendar days to include (default: 30, max: 365)",
                "minimum": 1,
                "maximum": 365,
            },
        },
        "required": [],
    },
}

GET_RISK_STATE = {
    "name": "get_risk_state",
    "description": (
        "Get the live risk engine state: open position count, daily P&L, drawdown percentage, "
        "consecutive loss count, whether trading is halted, and per-strategy cooldown status. "
        "Use to understand current risk posture before making recommendations."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

GET_LIVE_TICK = {
    "name": "get_live_tick",
    "description": (
        "Get the latest market tick (bid, ask, spread_bps, last price) for a symbol "
        "from the live tick cache. Use to check current spread conditions "
        "before evaluating signal quality or execution feasibility."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "venue": {
                "type": "string",
                "description": "Exchange venue: binance, oanda",
            },
            "symbol": {
                "type": "string",
                "description": "Symbol in CCXT format, e.g. BTC/USDT or EUR_USD",
            },
        },
        "required": ["venue", "symbol"],
    },
}

RUN_FUNDING_ARB_BACKTEST = {
    "name": "run_funding_arb_backtest",
    "description": (
        "Run a funding rate arbitrage backtest for the specified symbol and lookback period. "
        "Returns Sharpe ratio, max drawdown, win rate, total return, and a trade log. "
        "Use to validate whether a symbol has historically offered good arb opportunities, "
        "or to understand expected performance vs. actual results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Binance symbol without slash, e.g. BTCUSDT, ETHUSDT, SOLUSDT",
            },
            "days": {
                "type": "integer",
                "description": "Historical lookback in days (default: 30, max: 90 for speed)",
                "minimum": 7,
                "maximum": 90,
            },
            "min_edge_bps": {
                "type": "number",
                "description": "Minimum funding edge above fees in bps (default: 5.0)",
            },
        },
        "required": ["symbol"],
    },
}

RUN_STAT_ARB_BACKTEST = {
    "name": "run_stat_arb_backtest",
    "description": (
        "Run a statistical arbitrage (pairs mean-reversion) backtest. "
        "Returns Sharpe ratio, max drawdown, win rate, and Z-score distribution. "
        "Use to validate cointegration quality or diagnose stat_arb underperformance."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "spot_symbol": {
                "type": "string",
                "description": "Spot leg symbol (Binance), e.g. BTCUSDT",
            },
            "perp_symbol": {
                "type": "string",
                "description": "Perp leg symbol (FAPI), e.g. BTCUSDT",
            },
            "days": {
                "type": "integer",
                "description": "Historical lookback in days (default: 60, max: 90)",
                "minimum": 14,
                "maximum": 90,
            },
            "entry_z": {
                "type": "number",
                "description": "Z-score entry threshold (default: 2.0)",
            },
        },
        "required": ["spot_symbol"],
    },
}

GET_MARKET_REGIME = {
    "name": "get_market_regime",
    "description": (
        "Get the latest cached market regime classification: trending, mean_reverting, "
        "volatile, ranging, or crisis. Also returns strategy fitness scores (0-100) "
        "for each strategy in the current regime. Returns null if no regime has been "
        "classified yet (operator must POST /ai/regime first)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

RUN_FUNDING_ARB_SWEEP = {
    "name": "run_funding_arb_sweep",
    "description": (
        "Run a funding rate arbitrage parameter sweep: test multiple min_edge_bps values "
        "in a single call and compare Sharpe ratio, return, drawdown, and trade count. "
        "Use to find the optimal entry threshold for a given symbol. "
        "Downloads data once and runs N simulations — efficient for sensitivity analysis."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Binance symbol without slash, e.g. BTCUSDT",
            },
            "days": {
                "type": "integer",
                "description": "Historical lookback in days (default: 30, max: 90)",
                "minimum": 7,
                "maximum": 90,
            },
            "min_edge_bps_values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "List of 2-12 min_edge_bps values to test, e.g. [3, 5, 7.5, 10]",
                "minItems": 2,
                "maxItems": 12,
            },
        },
        "required": ["symbol"],
    },
}

RUN_STAT_ARB_SWEEP = {
    "name": "run_stat_arb_sweep",
    "description": (
        "Run a statistical arbitrage parameter sweep: test multiple entry Z-score thresholds "
        "in a single call. Higher Z = fewer but higher-quality signals. "
        "Returns a sensitivity table to find the optimal entry threshold. "
        "Downloads data once and runs N simulations."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "spot_symbol": {
                "type": "string",
                "description": "Spot leg symbol, e.g. BTCUSDT",
            },
            "perp_symbol": {
                "type": "string",
                "description": "Perp leg symbol (defaults to spot_symbol if omitted)",
            },
            "days": {
                "type": "integer",
                "description": "Historical lookback in days (default: 60, max: 90)",
                "minimum": 14,
                "maximum": 90,
            },
            "entry_z_values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "List of 2-12 entry Z-score thresholds, e.g. [1.5, 2.0, 2.5, 3.0]",
                "minItems": 2,
                "maxItems": 12,
            },
        },
        "required": ["spot_symbol"],
    },
}

RUN_REGIME_SPLIT = {
    "name": "run_regime_split",
    "description": (
        "Run a backtest and split trade results by realised-volatility regime. "
        "Returns separate metrics for low_vol, mid_vol, and high_vol periods. "
        "Use to determine whether a strategy's edge is regime-dependent — "
        "if P&L concentrates in low_vol periods, reduce sizing in volatile markets."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "strategy": {
                "type": "string",
                "description": "Strategy to analyse: funding_arb or stat_arb",
                "enum": ["funding_arb", "stat_arb"],
            },
            "symbol": {
                "type": "string",
                "description": "Primary symbol, e.g. BTCUSDT",
            },
            "days": {
                "type": "integer",
                "description": "Historical lookback in days (default: 60, max: 90)",
                "minimum": 14,
                "maximum": 90,
            },
            "min_edge_bps": {
                "type": "number",
                "description": "For funding_arb: minimum edge threshold (default: 5.0)",
            },
            "entry_z": {
                "type": "number",
                "description": "For stat_arb: Z-score entry threshold (default: 2.0)",
            },
        },
        "required": ["strategy", "symbol"],
    },
}

GET_SIGNAL_FUNNEL = {
    "name": "get_signal_funnel",
    "description": (
        "Get opportunity funnel statistics: how many signals were detected, "
        "AI-scored, risk-approved, and executed. Also returns rejection breakdown "
        "and filter rates. Use to understand pipeline health and where signals are dropping out."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "since_days": {
                "type": "integer",
                "description": "How many days back to analyse (default: 7)",
                "minimum": 1,
                "maximum": 90,
            },
            "strategy_type": {
                "type": "string",
                "description": "Filter to a single strategy: funding_arb, stat_arb, swing",
            },
        },
        "required": [],
    },
}

# ── Master tool list ──────────────────────────────────────────────────────────

ALL_TOOLS = [
    SEARCH_KNOWLEDGE_BASE,
    GET_TRADES,
    GET_TRADE_DETAILS,
    GET_PNL_SUMMARY,
    GET_RISK_STATE,
    GET_LIVE_TICK,
    RUN_FUNDING_ARB_BACKTEST,
    RUN_STAT_ARB_BACKTEST,
    RUN_FUNDING_ARB_SWEEP,
    RUN_STAT_ARB_SWEEP,
    RUN_REGIME_SPLIT,
    GET_MARKET_REGIME,
    GET_SIGNAL_FUNNEL,
]

# Subsets for specific agents (keeps context focused)
RESEARCH_TOOLS = [
    SEARCH_KNOWLEDGE_BASE,
    GET_TRADES,
    GET_PNL_SUMMARY,
    GET_RISK_STATE,
    RUN_FUNDING_ARB_BACKTEST,
    RUN_STAT_ARB_BACKTEST,
    RUN_FUNDING_ARB_SWEEP,
    RUN_STAT_ARB_SWEEP,
    RUN_REGIME_SPLIT,
    GET_MARKET_REGIME,
    GET_SIGNAL_FUNNEL,
]

TRADE_INVESTIGATION_TOOLS = [
    GET_TRADE_DETAILS,
    GET_TRADES,
    RUN_FUNDING_ARB_BACKTEST,
    RUN_STAT_ARB_BACKTEST,
    GET_LIVE_TICK,
    SEARCH_KNOWLEDGE_BASE,
    GET_MARKET_REGIME,
]

PORTFOLIO_TOOLS = [
    GET_RISK_STATE,
    GET_TRADES,
    GET_PNL_SUMMARY,
    GET_MARKET_REGIME,
    GET_LIVE_TICK,
    GET_SIGNAL_FUNNEL,
]


# ── Tool context object passed to executor ────────────────────────────────────

class ToolContext:
    """Holds HTTP client + Redis for tool execution."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        redis: Redis,
        journal_url: str,
        backtest_url: str,
        rag_url: str,
        risk_url: str,
    ):
        self.http = http_client
        self.redis = redis
        self.journal_url = journal_url.rstrip("/")
        self.backtest_url = backtest_url.rstrip("/")
        self.rag_url = rag_url.rstrip("/")
        self.risk_url = risk_url.rstrip("/")


# ── Individual tool implementations ──────────────────────────────────────────

async def _search_knowledge_base(inp: dict, ctx: ToolContext) -> dict:
    payload = {"question": inp["query"]}
    if inp.get("category"):
        payload["category"] = inp["category"]
    try:
        resp = await ctx.http.post(f"{ctx.rag_url}/query", json=payload, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        return {
            "answer": data.get("answer", ""),
            "sources": data.get("sources", []),
            "chunks_used": data.get("chunks_used", 0),
        }
    except Exception as exc:
        return {"error": f"Knowledge base search failed: {str(exc)[:100]}"}


async def _get_trades(inp: dict, ctx: ToolContext) -> dict:
    from datetime import datetime, timezone, timedelta
    days = min(int(inp.get("since_days", 7)), 90)
    since_dt = datetime.now(timezone.utc) - timedelta(days=days)
    params: dict[str, Any] = {
        "since": since_dt.isoformat(),
        "limit": min(int(inp.get("limit", 50)), 100),
    }
    if inp.get("strategy_type"):
        params["strategy_type"] = inp["strategy_type"]
    if inp.get("status"):
        params["status"] = inp["status"]
    if inp.get("venue"):
        params["venue"] = inp["venue"]
    try:
        resp = await ctx.http.get(f"{ctx.journal_url}/trades", params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        trades = data.get("trades", [])
        # Summarise for Claude — full trade objects are too verbose
        summary = []
        for t in trades:
            summary.append({
                "client_order_id": t.get("client_order_id"),
                "symbol": t.get("symbol"),
                "strategy_type": t.get("strategy_type"),
                "venue": t.get("venue"),
                "side": t.get("side"),
                "status": t.get("status"),
                "filled_qty": t.get("filled_qty"),
                "average_fill_price": t.get("average_fill_price"),
                "fee": t.get("fee"),
                "slippage_bps": t.get("slippage_bps"),
                "opened_at": t.get("opened_at"),
                "rejection_reason": t.get("rejection_reason"),
            })
        return {
            "total": data.get("total", len(trades)),
            "returned": len(trades),
            "since_days": days,
            "trades": summary,
        }
    except Exception as exc:
        return {"error": f"Failed to fetch trades: {str(exc)[:100]}"}


async def _get_trade_details(inp: dict, ctx: ToolContext) -> dict:
    coid = inp.get("client_order_id", "")
    if not coid:
        return {"error": "client_order_id is required"}
    try:
        resp = await ctx.http.get(f"{ctx.journal_url}/trades/{coid}", timeout=10.0)
        if resp.status_code == 404:
            return {"error": f"Trade not found: {coid}"}
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"error": f"Failed to fetch trade: {str(exc)[:100]}"}


async def _get_pnl_summary(inp: dict, ctx: ToolContext) -> dict:
    days = min(int(inp.get("days", 30)), 365)
    try:
        resp = await ctx.http.get(
            f"{ctx.journal_url}/pnl/summary", params={"days": days}, timeout=10.0
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"error": f"Failed to fetch P&L summary: {str(exc)[:100]}"}


async def _get_risk_state(inp: dict, ctx: ToolContext) -> dict:
    try:
        resp = await ctx.http.get(f"{ctx.risk_url}/health/state", timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"error": f"Failed to fetch risk state: {str(exc)[:100]}"}


async def _get_live_tick(inp: dict, ctx: ToolContext) -> dict:
    venue = inp.get("venue", "")
    symbol = inp.get("symbol", "")
    if not venue or not symbol:
        return {"error": "venue and symbol are required"}
    key = RedisKeys.latest_tick(venue, symbol)
    try:
        tick = await ctx.redis.hgetall(key)
        if not tick:
            return {"error": f"No tick data for {venue}:{symbol} — market-data may not be running"}
        return {
            "venue": venue,
            "symbol": symbol,
            "bid": tick.get("bid"),
            "ask": tick.get("ask"),
            "last": tick.get("last"),
            "spread_bps": tick.get("spread_bps"),
            "timestamp": tick.get("timestamp"),
        }
    except Exception as exc:
        return {"error": f"Redis tick lookup failed: {str(exc)[:100]}"}


async def _run_funding_arb_backtest(inp: dict, ctx: ToolContext) -> dict:
    payload = {
        "symbol": inp.get("symbol", "BTCUSDT"),
        "days": min(int(inp.get("days", 30)), 90),
        "min_edge_bps": float(inp.get("min_edge_bps", 5.0)),
        "capital_usd": 5000.0,
        "fee_bps": 7.5,
    }
    try:
        resp = await ctx.http.post(
            f"{ctx.backtest_url}/backtest/funding-arb", json=payload, timeout=60.0
        )
        resp.raise_for_status()
        data = resp.json()
        # Return summary only (trade log is too large for context)
        return {
            "symbol": payload["symbol"],
            "days": payload["days"],
            "total_trades": data.get("total_trades"),
            "win_rate": data.get("win_rate"),
            "total_return_pct": data.get("total_return_pct"),
            "sharpe_ratio": data.get("sharpe_ratio"),
            "max_drawdown_pct": data.get("max_drawdown_pct"),
            "avg_edge_bps": data.get("avg_edge_bps"),
            "total_fees_usd": data.get("total_fees_usd"),
            "note": "Full trade log omitted. Ask for specific trades if needed.",
        }
    except Exception as exc:
        return {"error": f"Funding arb backtest failed: {str(exc)[:100]}"}


async def _run_stat_arb_backtest(inp: dict, ctx: ToolContext) -> dict:
    spot = inp.get("spot_symbol", "BTCUSDT")
    payload = {
        "spot_symbol": spot,
        "perp_symbol": inp.get("perp_symbol", spot),
        "days": min(int(inp.get("days", 60)), 90),
        "entry_z": float(inp.get("entry_z", 2.0)),
        "exit_z": 0.5,
        "capital_usd": 5000.0,
        "fee_bps": 7.5,
        "interval": "1h",
        "window": 100,
    }
    try:
        resp = await ctx.http.post(
            f"{ctx.backtest_url}/backtest/stat-arb", json=payload, timeout=60.0
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "spot_symbol": spot,
            "days": payload["days"],
            "total_trades": data.get("total_trades"),
            "win_rate": data.get("win_rate"),
            "total_return_pct": data.get("total_return_pct"),
            "sharpe_ratio": data.get("sharpe_ratio"),
            "max_drawdown_pct": data.get("max_drawdown_pct"),
            "avg_hold_hours": data.get("avg_hold_hours"),
            "note": "Full trade log omitted. Ask for specific trades if needed.",
        }
    except Exception as exc:
        return {"error": f"Stat arb backtest failed: {str(exc)[:100]}"}


async def _run_funding_arb_sweep(inp: dict, ctx: ToolContext) -> dict:
    symbol = inp.get("symbol", "BTCUSDT")
    days = min(int(inp.get("days", 30)), 90)
    edge_values = inp.get("min_edge_bps_values", [3.0, 5.0, 7.5, 10.0])
    payload = {
        "symbol": symbol,
        "days": days,
        "capital_usd": 5000.0,
        "fee_bps": 7.5,
        "min_edge_bps_values": edge_values,
    }
    try:
        resp = await ctx.http.post(
            f"{ctx.backtest_url}/backtest/funding-arb/sweep", json=payload, timeout=90.0
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "symbol": symbol,
            "days": days,
            "sweep_param": "min_edge_bps",
            "results": data.get("results", []),
            "optimal_by_sharpe": data.get("optimal_by_sharpe"),
            "optimal_by_return": data.get("optimal_by_return"),
            "note": data.get("note", ""),
        }
    except Exception as exc:
        return {"error": f"Funding arb sweep failed: {str(exc)[:100]}"}


async def _run_stat_arb_sweep(inp: dict, ctx: ToolContext) -> dict:
    spot = inp.get("spot_symbol", "BTCUSDT")
    perp = inp.get("perp_symbol", spot)
    days = min(int(inp.get("days", 60)), 90)
    z_values = inp.get("entry_z_values", [1.5, 2.0, 2.5, 3.0])
    payload = {
        "spot_symbol": spot,
        "perp_symbol": perp,
        "days": days,
        "capital_usd": 5000.0,
        "fee_bps": 7.5,
        "entry_z_values": z_values,
    }
    try:
        resp = await ctx.http.post(
            f"{ctx.backtest_url}/backtest/stat-arb/sweep", json=payload, timeout=90.0
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "spot_symbol": spot,
            "days": days,
            "sweep_param": "entry_z",
            "results": data.get("results", []),
            "optimal_by_sharpe": data.get("optimal_by_sharpe"),
            "optimal_by_return": data.get("optimal_by_return"),
            "note": data.get("note", ""),
        }
    except Exception as exc:
        return {"error": f"Stat arb sweep failed: {str(exc)[:100]}"}


async def _run_regime_split(inp: dict, ctx: ToolContext) -> dict:
    strategy = inp.get("strategy", "funding_arb")
    symbol = inp.get("symbol", "BTCUSDT")
    days = min(int(inp.get("days", 60)), 90)
    payload: dict[str, Any] = {
        "strategy": strategy,
        "symbol": symbol,
        "days": days,
        "capital_usd": 5000.0,
        "fee_bps": 7.5,
        "min_edge_bps": float(inp.get("min_edge_bps", 5.0)),
        "entry_z": float(inp.get("entry_z", 2.0)),
        "perp_symbol": inp.get("perp_symbol", symbol),
    }
    try:
        resp = await ctx.http.post(
            f"{ctx.backtest_url}/backtest/regime-split", json=payload, timeout=90.0
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "strategy": strategy,
            "symbol": symbol,
            "days": days,
            "total_trades": data.get("total_trades"),
            "overall_metrics": data.get("overall_metrics", {}),
            "regime_split": data.get("regime_split", {}),
            "sizing_guidance": data.get("sizing_guidance", ""),
        }
    except Exception as exc:
        return {"error": f"Regime split backtest failed: {str(exc)[:100]}"}


async def _get_market_regime(inp: dict, ctx: ToolContext) -> dict:
    try:
        cached = await ctx.redis.get("ai:regime:latest")
        if cached:
            data = json.loads(cached)
            return {
                "regime": data.get("regime"),
                "confidence": data.get("confidence"),
                "regime_summary": data.get("regime_summary"),
                "strategy_fitness": data.get("strategy_fitness"),
                "risk_adjustment": data.get("risk_adjustment"),
                "key_indicators": data.get("key_indicators"),
                "classified_at": data.get("timestamp"),
                "cached": True,
            }
        return {
            "error": "No regime classification cached yet",
            "hint": "POST /ai/regime with current market data to generate a classification",
        }
    except Exception as exc:
        return {"error": f"Regime cache lookup failed: {str(exc)[:100]}"}


async def _get_signal_funnel(inp: dict, ctx: ToolContext) -> dict:
    from datetime import datetime, timezone, timedelta
    days = min(int(inp.get("since_days", 7)), 90)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    params: dict[str, Any] = {"since": since, "limit": 1, "offset": 0}
    if inp.get("strategy_type"):
        params["strategy_type"] = inp["strategy_type"]

    try:
        # Fetch opportunity counts from journal
        resp = await ctx.http.get(
            f"{ctx.journal_url}/opportunities", params=params, timeout=10.0
        )
        resp.raise_for_status()
        data = resp.json()

        # Build funnel from totals
        total = data.get("total", 0)
        # Fetch risk-approved count
        params_approved = {**params, "risk_approved": "true"}
        resp2 = await ctx.http.get(
            f"{ctx.journal_url}/opportunities", params=params_approved, timeout=10.0
        )
        approved_total = resp2.json().get("total", 0) if resp2.status_code == 200 else 0

        params_exec = {**params, "executed": "true"}
        resp3 = await ctx.http.get(
            f"{ctx.journal_url}/opportunities", params=params_exec, timeout=10.0
        )
        exec_total = resp3.json().get("total", 0) if resp3.status_code == 200 else 0

        return {
            "since_days": days,
            "strategy_type": inp.get("strategy_type"),
            "detected": total,
            "risk_approved": approved_total,
            "executed": exec_total,
            "risk_rejected": total - approved_total,
            "risk_approval_rate": round(approved_total / total, 3) if total else 0,
            "execution_rate": round(exec_total / approved_total, 3) if approved_total else 0,
        }
    except Exception as exc:
        return {"error": f"Failed to fetch signal funnel: {str(exc)[:100]}"}


# ── Tool dispatcher ───────────────────────────────────────────────────────────

_TOOL_MAP = {
    "search_knowledge_base": _search_knowledge_base,
    "get_trades": _get_trades,
    "get_trade_details": _get_trade_details,
    "get_pnl_summary": _get_pnl_summary,
    "get_risk_state": _get_risk_state,
    "get_live_tick": _get_live_tick,
    "run_funding_arb_backtest": _run_funding_arb_backtest,
    "run_stat_arb_backtest": _run_stat_arb_backtest,
    "run_funding_arb_sweep": _run_funding_arb_sweep,
    "run_stat_arb_sweep": _run_stat_arb_sweep,
    "run_regime_split": _run_regime_split,
    "get_market_regime": _get_market_regime,
    "get_signal_funnel": _get_signal_funnel,
}


async def execute_tool(tool_name: str, tool_input: dict, ctx: ToolContext) -> Any:
    """
    Dispatch a tool call by name. Returns a JSON-serialisable result or error dict.
    Never raises.
    """
    fn = _TOOL_MAP.get(tool_name)
    if fn is None:
        log.warning("agent.unknown_tool", tool=tool_name)
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return await fn(tool_input, ctx)
    except Exception as exc:
        log.error("agent.tool_dispatch_error", tool=tool_name, error=str(exc))
        return {"error": f"Tool execution error: {str(exc)[:100]}"}
