// ─── API Types — matches FastAPI service response models ──────────────────

export type StrategyType =
  | "funding_arb"
  | "stat_arb"
  | "swing"
  | "breakout"
  | "mean_reversion_scalp"
  | "momentum";

export type TradingMode = "paper" | "live" | "halted";

export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  service: string;
  version: string;
  trading_mode: TradingMode;
  timestamp: string;
  dependencies: Record<string, string> | null;
}

export interface Trade {
  id: string;
  opportunity_id: string | null;
  venue: "binance" | "oanda" | "mt5" | "paper";
  exchange_order_id: string | null;
  client_order_id: string;
  symbol: string;
  side: "buy" | "sell";
  order_type: "market" | "limit" | "stop";
  quantity: number;
  limit_price: number | null;
  filled_qty: number;
  average_fill_price: number | null;
  fee: number | null;
  fee_currency: string | null;
  slippage_bps: number | null;
  status: "pending" | "open" | "filled" | "cancelled" | "rejected";
  strategy_type: StrategyType | null;
  paper_mode: boolean;
  rejection_reason: string | null;
  realized_pnl: number | null;
  realized_pnl_currency: string | null;
  opened_at: string;
  filled_at: string | null;
  closed_at: string | null;
  updated_at: string;
}

export interface PnLSummary {
  total_realized_pnl: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  average_win: number;
  average_loss: number;
  profit_factor: number;
  max_drawdown_pct: number;
  sharpe_ratio: number | null;
}

export interface Opportunity {
  id: string;
  strategy_type: StrategyType;
  venue: string;
  symbol_primary: string;
  symbol_secondary: string | null;
  detected_at: string;
  spread: number | null;
  z_score: number | null;
  net_edge_bps: number | null;
  ai_score: number | null;
  ai_reasoning: string | null;
  ai_timeout: boolean;
  risk_approved: boolean | null;
  risk_rejection_reason: string | null;
  executed: boolean;
  expired: boolean;
  paper_mode: boolean;
}

export interface StrategyConfig {
  id: string;
  strategy_type: StrategyType;
  enabled: boolean;
  paper_mode: boolean;
  latency_profile: "standard" | "relaxed" | "fast";
  params: Record<string, unknown>;
  risk_overrides: Record<string, unknown>;
  updated_at: string;
  updated_by: string;
}

export interface RiskState {
  trading_mode: TradingMode;
  kill_switch_active: boolean;
  daily_drawdown_pct: number;
  max_daily_drawdown_pct: number;
  consecutive_losses: number;
  max_consecutive_losses: number;
  open_positions: number;
  max_open_positions: number;
  in_cooldown: boolean;
  cooldown_until: string | null;
}

export interface RagQuery {
  question: string;
  category?: string;
  top_k?: number;
}

export interface RagResponse {
  question: string;
  answer: string;
  sources: {
    title: string;
    score: number;
    chunk_index: number;
    page_start: number | null;
    page_end: number | null;
    is_table: boolean;
  }[];
  chunks_used: number;
  model: string;
  timed_out: boolean;
  retrieval_count: number;
}

export interface StrategyProfile {
  source_id: string;
  title: string;
  filename: string;
  strategy_name: string;
  strategy_type: string;
  confidence: "low" | "medium" | "high";
  core_thesis: string;
  entry_criteria: string[];
  exit_criteria: string[];
  risk_rules: {
    max_risk_per_trade_pct: number | null;
    max_drawdown_pct: number | null;
    position_sizing_method: string | null;
    stop_loss_method: string | null;
    take_profit_method: string | null;
    max_positions: number | null;
    notes: string[];
  };
  instruments: string[];
  timeframes: string[];
  expected_win_rate: number | null;
  expected_rr_ratio: number | null;
  edge_source: string;
  key_principles: string[];
  implementation_notes: string;
  created_at: string;
}

// ── Backtest types — matches actual backtest engine output ─────────────────

export interface EquityCurvePoint {
  ts: string;         // ISO timestamp
  equity_usd: number;
  trade_pnl: number;
}

export interface BacktestResult {
  strategy: string;
  symbol: string;
  interval: string;
  start_dt: string;
  end_dt: string;
  capital_usd: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl_usd: number;
  total_fees_usd: number;
  net_pnl_usd: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  avg_hold_candles: number;
  total_return_pct: number;
  equity_curve: EquityCurvePoint[];
  trade_log: Record<string, unknown>[];
  params: Record<string, unknown>;
}

export interface MonteCarloResult {
  n_simulations: number;
  n_trades: number;
  capital_usd: number;
  equity_p10: number;
  equity_p25: number;
  equity_p50: number;
  equity_p75: number;
  equity_p90: number;
  max_dd_p10: number;
  max_dd_p50: number;
  max_dd_p90: number;
  ruin_prob_25pct: number;
  ruin_prob_50pct: number;
  kelly_fraction: number;
  kelly_position_pct: number;
  strategy: string;
  symbol: string;
}

export interface GridSearchEntry {
  strategy: string;
  symbol: string;
  params: Record<string, number>;
  sharpe_ratio: number;
  net_pnl_usd: number;
  total_trades: number;
  win_rate: number;
  max_drawdown_pct: number;
  total_return_pct: number;
  kelly_fraction: number;
}

// ── Strategy operational status ────────────────────────────────────────────

export interface StrategyRotationEntry {
  consecutive_losses: number;
  degraded: boolean;
  threshold: number;
}

export interface StrategyEdgeEntry {
  win_rate: number | null;
  window_size: number;
  decayed: boolean;
  recent: number[];   // sparkline bits
}

export interface StrategyDrawdownEntry {
  cum_pnl_usd: number | null;
  drawdown_pct: number | null;
  avg_win_usd: number | null;
  avg_loss_usd: number | null;
}

export interface StrategyOverviewEntry {
  rotation: StrategyRotationEntry;
  edge: StrategyEdgeEntry;
  drawdown: StrategyDrawdownEntry;
}

export interface StrategyOverview {
  current_regime: string;
  local_regime: string | null;
  preferred_strategy: string | null;
  baseline_win_rate: number;
  rotation_threshold: number;
  strategies: Record<StrategyType, StrategyOverviewEntry>;
  timestamp: string;
}

// ── OHLCV candles (persisted bars → lightweight-charts) ────────────────────

export interface OHLCVCandle {
  ts: number;        // epoch milliseconds (bucket start)
  ts_dt?: string;    // ISO timestamp
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  mid?: number;
}

// ── Regime ────────────────────────────────────────────────────────────────

export interface RegimeResponse {
  regime: string;
  confidence: number;
  regime_summary?: string;
  strategy_fitness?: Record<string, number>;
  risk_adjustment?: string;
  key_indicators?: string[];
  timestamp: string;
}
