// ─── API Types — matches FastAPI service response models ──────────────────

export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  service: string;
  version: string;
  trading_mode: "paper" | "live" | "halted";
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
  strategy_type: string | null;
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
  strategy_type: string;
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
  strategy_type: "funding_arb" | "stat_arb" | "swing";
  enabled: boolean;
  paper_mode: boolean;
  latency_profile: "standard" | "relaxed" | "aggressive";
  params: Record<string, unknown>;
  risk_overrides: Record<string, unknown>;
  updated_at: string;
  updated_by: string;
}

export interface RiskState {
  trading_mode: "paper" | "live" | "halted";
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

export interface BacktestResult {
  strategy_type: string;
  start_date: string;
  end_date: string;
  total_trades: number;
  win_rate: number;
  profit_factor: number;
  sharpe_ratio: number | null;
  max_drawdown_pct: number;
  total_return_pct: number;
  annualised_return_pct: number | null;
  equity_curve: { date: string; equity: number }[];
}
