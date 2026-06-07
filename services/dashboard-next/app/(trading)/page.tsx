"use client";

import { Suspense } from "react";
import { StatCard }        from "@/components/trading/stat-card";
import { RecentSignals }   from "@/components/trading/recent-signals";
import { RegimeBadge }     from "@/components/trading/regime-badge";
import { RiskMeterCompact } from "@/components/trading/risk-meter-compact";
import { PriceGrid }       from "@/components/trading/price-grid";
import { AiDigest }        from "@/components/trading/ai-digest";
import { StrategyHealth }  from "@/components/trading/strategy-health";
import { api }             from "@/lib/api";
import { TrendingUp, TrendingDown, Activity, BarChart2 } from "lucide-react";

function PanelSkeleton({ h = 72 }: { h?: number }) {
  return <div className={`panel h-${h} animate-pulse`} />;
}

export default function DashboardPage() {
  return (
    <div className="space-y-5">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
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
          queryKey={["journal", "pnl"]}
          queryFn={() => api.journal.pnl() as unknown as Promise<Record<string, unknown>>}
          valueKey="total_realized_pnl"
          prefix="$"
          signed
          refetchInterval={15_000}
          icon={<TrendingUp size={14} />}
        />
        <StatCard
          label="Win Rate"
          queryKey={["journal", "pnl"]}
          queryFn={() => api.journal.pnl() as unknown as Promise<Record<string, unknown>>}
          valueKey="win_rate"
          suffix="%"
          decimals={1}
          refetchInterval={15_000}
          icon={<BarChart2 size={14} />}
        />
        <StatCard
          label="Open Positions"
          queryKey={["risk", "state"]}
          queryFn={() => api.risk.state() as unknown as Promise<Record<string, unknown>>}
          valueKey="open_positions"
          refetchInterval={5_000}
          icon={<Activity size={14} />}
        />
        <StatCard
          label="Daily Drawdown"
          queryKey={["risk", "state"]}
          queryFn={() => api.risk.state() as unknown as Promise<Record<string, unknown>>}
          valueKey="daily_drawdown_pct"
          suffix="%"
          decimals={2}
          invert
          refetchInterval={5_000}
          icon={<TrendingDown size={14} />}
        />
      </div>

      {/* Main grid — price charts + risk/AI */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <Suspense fallback={<PanelSkeleton h={72} />}>
            <PriceGrid />
          </Suspense>
        </div>
        <div className="space-y-4">
          <RiskMeterCompact />
          <Suspense fallback={<PanelSkeleton h={32} />}>
            <AiDigest />
          </Suspense>
        </div>
      </div>

      {/* Strategy health — rotation + edge + drawdown */}
      <Suspense fallback={<PanelSkeleton h={48} />}>
        <StrategyHealth />
      </Suspense>

      {/* Signal feed */}
      <Suspense fallback={<PanelSkeleton h={48} />}>
        <RecentSignals />
      </Suspense>

    </div>
  );
}
