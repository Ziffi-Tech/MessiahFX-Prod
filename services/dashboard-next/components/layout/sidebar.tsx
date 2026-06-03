"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";
import {
  LayoutDashboard,
  TrendingUp,
  Settings2,
  BarChart3,
  Shield,
  BookOpen,
  ScrollText,
  BellRing,
  Settings,
  LogOut,
  Bot,
} from "lucide-react";

const NAV = [
  { href: "/",            label: "Dashboard",   icon: LayoutDashboard },
  { href: "/positions",   label: "Positions",   icon: TrendingUp },
  { href: "/strategies",  label: "Strategies",  icon: Settings2 },
  { href: "/backtest",    label: "Backtest",     icon: BarChart3 },
  { href: "/risk",        label: "Risk",         icon: Shield },
  { href: "/journal",     label: "Journal",      icon: ScrollText },
  { href: "/rag",         label: "RAG Studio",   icon: BookOpen },
];

const BOTTOM_NAV = [
  { href: "/settings",    label: "Settings",    icon: Settings },
];

export function Sidebar() {
  const path = usePathname();

  const isActive = (href: string) =>
    href === "/" ? path === "/" : path.startsWith(href);

  return (
    <aside
      className="flex flex-col w-[220px] shrink-0 border-r"
      style={{
        background: "var(--bg-surface)",
        borderColor: "var(--border)",
        height: "100dvh",
      }}
    >
      {/* Logo */}
      <div
        className="flex items-center gap-2.5 px-5 h-14 border-b shrink-0"
        style={{ borderColor: "var(--border)" }}
      >
        <div
          className="w-7 h-7 rounded flex items-center justify-center text-xs font-black"
          style={{ background: "var(--blue)", color: "#fff" }}
        >
          MX
        </div>
        <div>
          <div className="text-sm font-bold tracking-tight" style={{ color: "var(--text-primary)" }}>
            MeznaFX
          </div>
          <div className="text-[10px] font-medium" style={{ color: "var(--text-secondary)" }}>
            QUANT TRADING
          </div>
        </div>
      </div>

      {/* Trading mode badge */}
      <div className="px-4 py-3 shrink-0">
        <div className="badge badge-orange w-full justify-center">
          PAPER MODE
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-1">
        <div className="space-y-0.5">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded text-sm font-medium transition-colors",
                isActive(href)
                  ? "text-white"
                  : "hover:bg-[var(--bg-hover)]"
              )}
              style={
                isActive(href)
                  ? { background: "var(--blue-dim)", color: "var(--blue)" }
                  : { color: "var(--text-secondary)" }
              }
            >
              <Icon size={15} />
              {label}
            </Link>
          ))}
        </div>

        {/* AI badge */}
        <div
          className="mt-4 mx-1 p-3 rounded-lg"
          style={{ background: "var(--purple-dim)", border: "1px solid rgba(167,139,250,0.2)" }}
        >
          <div className="flex items-center gap-2 mb-1.5">
            <Bot size={13} style={{ color: "var(--purple)" }} />
            <span className="text-xs font-semibold" style={{ color: "var(--purple)" }}>
              AI Filter
            </span>
            <span className="live-dot ml-auto" />
          </div>
          <p className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
            Claude scoring active
          </p>
        </div>
      </nav>

      {/* Bottom nav */}
      <div
        className="shrink-0 px-2 py-2 border-t space-y-0.5"
        style={{ borderColor: "var(--border)" }}
      >
        {BOTTOM_NAV.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className="flex items-center gap-3 px-3 py-2 rounded text-sm font-medium transition-colors hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-secondary)" }}
          >
            <Icon size={15} />
            {label}
          </Link>
        ))}

        <form action="/api/auth/logout" method="POST">
          <button
            type="submit"
            className="flex items-center gap-3 px-3 py-2 rounded text-sm font-medium w-full transition-colors hover:bg-[var(--red-dim)]"
            style={{ color: "var(--text-secondary)" }}
          >
            <LogOut size={15} />
            Sign out
          </button>
        </form>
      </div>
    </aside>
  );
}
