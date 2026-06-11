"use client";

import { useEffect, useRef, useState } from "react";
import { LayoutGrid, Eye, EyeOff, Check } from "lucide-react";
import {
  useWorkspaceStore, WORKSPACE_NAMES, PANELS,
} from "@/lib/stores/workspace";

/** Rehydrates the persisted workspace after mount (avoids SSR mismatch). */
export function WorkspaceHydration() {
  useEffect(() => {
    void useWorkspaceStore.persist.rehydrate();
  }, []);
  return null;
}

export function WorkspaceSwitcher() {
  const active = useWorkspaceStore((s) => s.active);
  const setWorkspace = useWorkspaceStore((s) => s.setWorkspace);
  const togglePanel = useWorkspaceStore((s) => s.togglePanel);
  const visible = useWorkspaceStore((s) => s.visible);

  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close the panel menu on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="flex items-center gap-1.5" ref={menuRef}>
      {/* Preset tabs */}
      <div className="flex items-center gap-0.5" role="tablist" aria-label="Workspace">
        {WORKSPACE_NAMES.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={active === name}
            onClick={() => setWorkspace(name)}
            className="text-[10px] mono px-1.5 py-1 rounded capitalize"
            style={active === name
              ? { background: "var(--blue-dim)", color: "var(--blue)" }
              : { color: "var(--text-tertiary)" }}
          >
            {name}
          </button>
        ))}
        {active === "custom" && (
          <span className="text-[10px] mono px-1.5 py-1 rounded capitalize"
                style={{ background: "var(--purple-dim)", color: "var(--purple)" }}>
            custom
          </span>
        )}
      </div>

      {/* Panel visibility menu */}
      <div className="relative">
        <button
          type="button"
          aria-label="Configure visible panels"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="p-1.5 rounded hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-secondary)" }}
        >
          <LayoutGrid size={13} />
        </button>

        {open && (
          <div
            className="absolute right-0 top-8 z-30 w-52 rounded-lg py-1 shadow-xl"
            style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}
            role="menu"
            aria-label="Panel visibility"
          >
            <div className="px-3 py-1.5 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
              Visible panels — toggling forks to “custom”
            </div>
            {PANELS.map((p) => {
              const on = visible(p.id);
              return (
                <button
                  key={p.id}
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={on}
                  onClick={() => togglePanel(p.id)}
                  className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-left hover:bg-[var(--bg-hover)]"
                  style={{ color: on ? "var(--text-primary)" : "var(--text-tertiary)" }}
                >
                  {on ? <Eye size={12} /> : <EyeOff size={12} />}
                  <span className="flex-1">{p.label}</span>
                  {on && <Check size={12} style={{ color: "var(--green)" }} />}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
