"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  LayoutDashboard, TrendingUp, Settings2, BarChart3, Shield, ScrollText,
  BookOpen, Settings, Play, Square, AlertOctagon, Search, CornerDownLeft, LineChart,
} from "lucide-react";
import { useBotStart, useBotStop, useKillSwitch, useAuth } from "@/lib/hooks";

interface Command {
  id: string;
  label: string;
  group: "Navigate" | "Actions";
  icon: React.ComponentType<{ size?: number }>;
  run: () => void;
  danger?: boolean;
}

/**
 * Keyboard-first command palette — the terminal's ⌘K / Ctrl-K entry point.
 * Jump to any page or fire a bot action without touching the mouse.
 * Mounted once in the trading layout.
 */
export function CommandPalette() {
  const router = useRouter();
  const { data: auth } = useAuth();
  const start = useBotStart();
  const stop = useBotStop();
  const kill = useKillSwitch();
  const canAct = auth?.role !== "viewer";

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const commands = useMemo<Command[]>(() => {
    const nav = (id: string, label: string, icon: Command["icon"]): Command => ({
      id, label, group: "Navigate", icon, run: () => router.push(id),
    });
    const list: Command[] = [
      nav("/", "Dashboard", LayoutDashboard),
      nav("/positions", "Positions & P&L", TrendingUp),
      nav("/strategies", "Strategy Controls", Settings2),
      nav("/backtest", "Backtest & Optimiser", BarChart3),
      nav("/performance", "Performance & TCA", LineChart),
      nav("/risk", "Risk Monitor", Shield),
      nav("/journal", "Trade Journal", ScrollText),
      nav("/rag", "RAG Studio", BookOpen),
      nav("/settings", "Settings", Settings),
    ];
    // Write actions are hidden for read-only (viewer) roles.
    if (canAct) {
      list.push(
        { id: "act:start", label: "Start bot (paper)", group: "Actions", icon: Play,
          run: () => start.mutate(true) },
        { id: "act:stop", label: "Stop bot", group: "Actions", icon: Square,
          run: () => { if (confirm("Stop the bot? Halts trading and disables all strategies.")) stop.mutate("Stop from command palette"); } },
        { id: "act:kill", label: "KILL — emergency halt", group: "Actions", icon: AlertOctagon, danger: true,
          run: () => { if (confirm("EMERGENCY KILL — halt ALL trading immediately?")) kill.mutate(true); } },
      );
    }
    return list;
  }, [router, start, stop, kill, canAct]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => c.label.toLowerCase().includes(q) || c.group.toLowerCase().includes(q));
  }, [commands, query]);

  // Global ⌘K / Ctrl-K toggle + a custom event so UI affordances can open it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("mezna:command-palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mezna:command-palette", onOpen);
    };
  }, []);

  // Reset + focus on open.
  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Keep the active row in range as the filter changes.
  useEffect(() => { setActive(0); }, [query]);

  if (!open) return null;

  const exec = (cmd?: Command) => {
    if (!cmd) return;
    setOpen(false);
    cmd.run();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { setOpen(false); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, filtered.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); exec(filtered[active]); }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh]"
      style={{ background: "rgba(0,0,0,0.55)" }}
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-lg rounded-lg overflow-hidden shadow-2xl"
        style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search */}
        <div className="flex items-center gap-2 px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
          <Search size={15} style={{ color: "var(--text-tertiary)" }} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Jump to… or run an action"
            className="flex-1 bg-transparent outline-none text-sm"
            style={{ color: "var(--text-primary)" }}
          />
          <kbd className="text-[10px] mono px-1.5 py-0.5 rounded" style={{ background: "var(--bg-surface-2)", color: "var(--text-tertiary)" }}>ESC</kbd>
        </div>

        {/* Results */}
        <div className="max-h-80 overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <div className="px-4 py-6 text-center text-xs" style={{ color: "var(--text-tertiary)" }}>
              No matching commands
            </div>
          ) : (
            filtered.map((cmd, i) => {
              const Icon = cmd.icon;
              const isActive = i === active;
              return (
                <button
                  key={cmd.id}
                  type="button"
                  onMouseEnter={() => setActive(i)}
                  onClick={() => exec(cmd)}
                  className="flex items-center gap-3 w-full px-4 py-2 text-sm text-left"
                  style={{
                    background: isActive ? "var(--bg-hover)" : "transparent",
                    color: cmd.danger ? "var(--red)" : "var(--text-primary)",
                  }}
                >
                  <Icon size={15} />
                  <span className="flex-1">{cmd.label}</span>
                  <span className="text-[10px] mono" style={{ color: "var(--text-tertiary)" }}>{cmd.group}</span>
                  {isActive && <CornerDownLeft size={12} style={{ color: "var(--text-tertiary)" }} />}
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
