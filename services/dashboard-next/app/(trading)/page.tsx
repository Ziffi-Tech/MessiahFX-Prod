import { Suspense } from "react";
import { StatCard } from "@/components/trading/stat-card";
import { RecentSignals } from "@/components/trading/recent-signals";
import { RegimeBadge } from "@/components/trading/regime-badge";
import { RiskMeterCompact } from "@/components/trading/risk-meter-compact";
import { PriceGrid } from "@/components/trading/price-grid";
import { AiDigest } from "@/components/trading/ai-digest";
import {
  TrendingUp, TrendingDown, Activity, BarChart2
} from "lucide-react";

export default function DashboardPage() {
  return (
    <div className="space-y-5">

      {/* Header row */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-base font-bold" style={{ color: "var(--text-primary)" }}>
            Live Dashboard
          </h1>
          <p className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
            Paper trading mode · positions update in real-time
          </p>
        </div>
        <Suspense fallback={null}>
          <RegimeBadge />
        </Suspense>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          label="Today P&L"
          valuePath="/api/gateway/journal/pnl"
          valueKey="total_realized_pnl"
          prefix="$"
          signed
          icon={<TrendingUp size={14} />}
        />
        <StatCard
          label="Win Rate"
          valuePath="/api/gateway/journal/pnl"
          valueKey="win_rate"
          suffix="%"
          decimals={1}
          icon={<BarChart2 size={14} />}
        />
        <StatCard
          label="Open Positions"
          valuePath="/api/gateway/risk/state"
          valueKey="open_positions"
          icon={<Activity size={14} />}
        />
        <StatCard
          label="Max Drawdown"
          valuePath="/api/gateway/risk/state"
          valueKey="daily_drawdown_pct"
          suffix="%"
          decimals={2}
          invert
          icon={<TrendingDown size={14} />}
        />
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Price charts — 2 cols */}
        <div className="lg:col-span-2">
          <Suspense fallback={<div className="panel h-72 animate-pulse" />}>
            <PriceGrid />
          </Suspense>
        </div>

        {/* Right column */}
        <div className="space-y-4">
          <RiskMeterCompact />
          <Suspense fallback={<div className="panel h-32 animate-pulse" />}>
            <AiDigest />
          </Suspense>
        </div>
      </div>

      {/* Signal feed */}
      <Suspense fallback={<div className="panel h-48 animate-pulse" />}>
        <RecentSignals />
      </Suspense>
    </div>
  );
}
