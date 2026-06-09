// ─── Typed API client — all calls go through Next.js /api/gateway proxy ──

import type {
  Trade, PnLSummary, Opportunity,
  StrategyConfig, RiskState, RagQuery, RagResponse,
  StrategyProfile, BacktestResult, MonteCarloResult,
  GridSearchEntry, StrategyOverview, RegimeResponse, OHLCVCandle, OrderBook,
} from "@/types/api";
import type { LiveTick } from "@/lib/stores/live";
import type { Role } from "@/lib/auth";

export interface AuthMe { authenticated: boolean; user?: string; role?: Role; }

const BASE = "/api/gateway";

async function req<T>(
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${method} ${path} → ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Raw backend response shapes (may differ from frontend types) ──────────────

interface RawRiskState {
  trading_halted: boolean;
  halt_reason: string | null;
  risk_state: {
    daily_pnl_usd: number;
    daily_drawdown_pct: number;
    open_position_count: number;
    consecutive_losses: number;
    last_updated: string;
  };
  cooldowns: Record<string, boolean>;
  limits: {
    max_per_trade_pct: number;
    max_daily_drawdown_pct: number;
    max_open_positions: number;
    max_consecutive_losses: number;
    cooldown_minutes: number;
    paper_capital_usd: number;
  };
}

interface RawPnLSummary {
  days: number;
  total_fills: number;
  total_notional: number;
  total_fees: number;
  realized_pnl: number;
  net_pnl: number;
  // Performance stats (added to /journal/pnl/summary). win_rate is a 0..1 fraction.
  winning_trades?: number;
  losing_trades?: number;
  win_rate?: number;
  average_win?: number;
  average_loss?: number;
  profit_factor?: number | null;
  max_drawdown_pct?: number;
  sharpe_ratio?: number | null;
}

// ── Normalisers ────────────────────────────────────────────────────────────────

function normaliseRiskState(raw: RawRiskState): RiskState {
  const rs = raw.risk_state ?? {};
  const limits = raw.limits ?? {};
  const inCooldown = Object.values(raw.cooldowns ?? {}).some(Boolean);
  return {
    trading_mode: raw.trading_halted ? "halted" : "paper",
    kill_switch_active: raw.trading_halted ?? false,
    daily_drawdown_pct:     rs.daily_drawdown_pct     ?? 0,
    max_daily_drawdown_pct: limits.max_daily_drawdown_pct ?? 3,
    consecutive_losses:     rs.consecutive_losses     ?? 0,
    max_consecutive_losses: limits.max_consecutive_losses ?? 5,
    open_positions:         rs.open_position_count    ?? 0,
    max_open_positions:     limits.max_open_positions ?? 5,
    in_cooldown: inCooldown,
    cooldown_until: null,
  };
}

function normalisePnL(raw: RawPnLSummary): PnLSummary {
  return {
    total_realized_pnl: raw.net_pnl        ?? 0,
    total_trades:       raw.total_fills    ?? 0,
    winning_trades:     raw.winning_trades ?? 0,
    losing_trades:      raw.losing_trades  ?? 0,
    win_rate:          (raw.win_rate ?? 0) * 100,   // backend sends a 0..1 fraction
    average_win:        raw.average_win    ?? 0,
    average_loss:       raw.average_loss   ?? 0,
    profit_factor:      raw.profit_factor  ?? 0,
    max_drawdown_pct:   raw.max_drawdown_pct ?? 0,
    sharpe_ratio:       raw.sharpe_ratio   ?? null,
  };
}

export const api = {
  // ── Health ─────────────────────────────────────────────────────────────────
  health: {
    live:  () => req<{ status: string }>("GET", "/health/live"),
    ready: () => req<{ status: string }>("GET", "/health/ready"),
  },

  // ── Market data ──────────────────────────────────────────────────────────────
  market: {
    // First-paint snapshot; the SSE stream takes over for live updates.
    ticksLatest: (venues?: string) =>
      req<{ ticks: LiveTick[]; count: number; timestamp: string }>(
        "GET", `/market-data/ticks/latest${venues ? `?venues=${encodeURIComponent(venues)}` : ""}`
      ),
    // Persisted OHLCV candles (served by the backtest service from ohlcv_bars).
    ohlcv: (params: { venue?: string; symbol?: string; interval?: string; days?: number }) => {
      const q = new URLSearchParams(
        Object.fromEntries(
          Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])
        )
      ).toString();
      return req<{
        status: string; venue: string; symbol: string; interval: string;
        count: number; candles: OHLCVCandle[];
      }>("GET", `/backtest/ohlcv${q ? `?${q}` : ""}`);
    },
    // L2 order book (depth ladder). 404 when ORDERBOOK_SYMBOLS excludes this symbol.
    orderbook: (venue: string, symbol: string) =>
      req<OrderBook>(
        "GET",
        `/market-data/orderbook/latest?venue=${encodeURIComponent(venue)}&symbol=${encodeURIComponent(symbol)}`,
      ),
  },

  // ── Auth (not under the gateway proxy) ───────────────────────────────────────
  auth: {
    me: async (): Promise<AuthMe> => {
      const res = await fetch("/api/auth", { cache: "no-store" });
      if (!res.ok) return { authenticated: false };
      return res.json() as Promise<AuthMe>;
    },
  },

  // ── System control (bot lifecycle) ───────────────────────────────────────────
  control: {
    status: () => req<Record<string, unknown>>("GET", "/api/v1/control/status"),
    botStart: (paperMode = true) =>
      req<Record<string, unknown>>("POST", "/api/v1/control/bot/start", {
        paper_mode: paperMode, started_by: "dashboard",
      }),
    botStop: (reason = "Manual stop from dashboard") =>
      req<Record<string, unknown>>("POST", "/api/v1/control/bot/stop", {
        stopped_by: "dashboard", reason,
      }),
  },

  // ── Journal ────────────────────────────────────────────────────────────────
  journal: {
    trades: (params?: { limit?: number; offset?: number; status?: string }) => {
      const q = new URLSearchParams(
        Object.fromEntries(
          Object.entries(params ?? {}).filter(([, v]) => v !== undefined)
        ) as Record<string, string>
      ).toString();
      return req<{ trades: Trade[]; total: number }>(
        "GET", `/journal/trades${q ? `?${q}` : ""}`
      );
    },
    pnl: async (): Promise<PnLSummary> => {
      const raw = await req<RawPnLSummary>("GET", "/journal/pnl/summary");
      return normalisePnL(raw);
    },
    opportunities: (limit = 20) =>
      req<{ opportunities: Opportunity[] }>(
        "GET", `/journal/opportunities?limit=${limit}`
      ),
  },

  // ── Strategies ─────────────────────────────────────────────────────────────
  strategy: {
    list: () =>
      req<{ strategies: StrategyConfig[] }>("GET", "/strategy/configs"),
    toggle: (id: string, enabled: boolean) =>
      req<StrategyConfig>("PATCH", `/strategy/configs/${id}`, { enabled }),
    update: (id: string, params: Record<string, unknown>) =>
      req<StrategyConfig>("PATCH", `/strategy/configs/${id}`, { params }),
    overview: () =>
      req<StrategyOverview>("GET", "/strategy/overview"),
  },

  // ── Risk ───────────────────────────────────────────────────────────────────
  risk: {
    state: async (): Promise<RiskState> => {
      const raw = await req<RawRiskState>("GET", "/risk/health/state");
      return normaliseRiskState(raw);
    },
    killSwitch: async (activate: boolean): Promise<{ success: boolean }> => {
      // Kill switch is on the gateway control plane, not the risk service.
      // Reset REQUIRES confirm:true + a reason (min 5 chars) — sending {} 400s.
      if (activate) {
        await req<unknown>("POST", "/api/v1/control/kill", {
          reason: "Dashboard kill switch",
          activated_by: "dashboard",
        });
      } else {
        await req<unknown>("POST", "/api/v1/control/reset", {
          confirm: true,
          reason: "Resume trading from dashboard",
          reset_by: "dashboard",
        });
      }
      return { success: true };
    },
  },

  // ── AI Filter / Regime ─────────────────────────────────────────────────────
  aiFilter: {
    regimeCached: () =>
      req<RegimeResponse>("GET", "/ai/regime/cached"),
    regimeAnalyse: (body: { data_points: unknown[]; operator_context?: string }) =>
      req<RegimeResponse>("POST", "/ai/regime", body),
    digest: () =>
      req<{ digest: string; generated_at: string }>("GET", "/ai/digest"),
  },

  // ── Backtest ───────────────────────────────────────────────────────────────
  backtest: {
    fundingArb: (body: {
      symbol?: string; days?: number; capital_usd?: number;
      min_edge_bps?: number; fee_bps?: number;
    }) => req<BacktestResult>("POST", "/backtest/funding-arb", body),

    statArb: (body: {
      spot_symbol?: string; perp_symbol?: string; interval?: string;
      days?: number; window?: number; entry_z?: number; exit_z?: number;
      capital_usd?: number; fee_bps?: number;
    }) => req<BacktestResult>("POST", "/backtest/stat-arb", body),

    monteCarloFundingArb: (body: {
      symbol?: string; days?: number; capital_usd?: number;
      min_edge_bps?: number; fee_bps?: number; n_simulations?: number;
    }) => req<{ backtest_summary: Partial<BacktestResult>; monte_carlo: MonteCarloResult; interpretation: Record<string, string> }>(
      "POST", "/backtest/monte-carlo/funding-arb", body
    ),

    monteCarloStatArb: (body: {
      spot_symbol?: string; perp_symbol?: string; interval?: string;
      days?: number; window?: number; entry_z?: number;
      capital_usd?: number; fee_bps?: number;
    }) => req<{ backtest_summary: Partial<BacktestResult>; monte_carlo: MonteCarloResult; interpretation: Record<string, string> }>(
      "POST", "/backtest/monte-carlo/stat-arb", body
    ),

    optimizeFundingArb: (body: {
      symbol?: string; days?: number; capital_usd?: number;
      min_edge_grid?: number[]; fee_grid?: number[];
    }) => req<{ ranked_results: GridSearchEntry[]; best_params: Record<string, number>; combinations_run: number }>(
      "POST", "/backtest/optimize/funding-arb", body
    ),

    optimizeStatArb: (body: {
      spot_symbol?: string; perp_symbol?: string; interval?: string;
      days?: number; capital_usd?: number;
      window_grid?: number[]; entry_z_grid?: number[];
    }) => req<{ ranked_results: GridSearchEntry[]; best_params: Record<string, number>; combinations_run: number; warning: string }>(
      "POST", "/backtest/optimize/stat-arb", body
    ),
  },

  // ── RAG ────────────────────────────────────────────────────────────────────
  rag: {
    query: (body: RagQuery) =>
      req<RagResponse>("POST", "/rag/query", body),
    strategies: () =>
      req<StrategyProfile[]>("GET", "/rag/strategies"),
    strategy: (id: string) =>
      req<StrategyProfile>("GET", `/rag/strategies/${id}`),
    deleteStrategy: (id: string) =>
      req<void>("DELETE", `/rag/strategies/${id}`),
    uploadPdf: async (file: File, title: string, category: string) => {
      const form = new FormData();
      form.append("file", file);
      form.append("title", title);
      form.append("category", category);
      const res = await fetch(`${BASE}/rag/ingest/pdf`, { method: "POST", body: form });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
  },
};
