"use client";

import { useState } from "react";
import { Settings, Key, Eye, EyeOff, Shield } from "lucide-react";

export default function SettingsPage() {
  const [showPass, setShowPass] = useState(false);
  const [newPass, setNewPass] = useState("");
  const [saved, setSaved] = useState(false);

  const savePassword = async () => {
    // Password is managed via .env — show instructions
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <div className="space-y-5 max-w-2xl">
      <h1 className="text-base font-bold" style={{ color: "var(--text-primary)" }}>Settings</h1>

      {/* Trading mode */}
      <div className="panel p-5 space-y-4">
        <div className="flex items-center gap-2">
          <Shield size={14} style={{ color: "var(--blue)" }} />
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Trading Mode</span>
        </div>
        <div
          className="flex items-center gap-3 p-4 rounded"
          style={{ background: "var(--orange-dim)", border: "1px solid rgba(251,146,60,0.3)" }}
        >
          <div>
            <p className="text-sm font-semibold" style={{ color: "var(--orange)" }}>Paper Trading Mode</p>
            <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>
              All orders are simulated. No real capital at risk.
              Change <code className="mono text-[10px] px-1 rounded" style={{ background: "var(--bg-surface-3)" }}>TRADING_MODE</code> in your <code className="mono text-[10px] px-1 rounded" style={{ background: "var(--bg-surface-3)" }}>.env</code> file to switch modes.
            </p>
          </div>
        </div>
        <div
          className="flex items-center gap-3 p-3 rounded text-xs"
          style={{ background: "var(--red-dim)", border: "1px solid rgba(255,61,87,0.2)" }}
        >
          <Shield size={12} style={{ color: "var(--red)" }} />
          <span style={{ color: "var(--text-secondary)" }}>
            Never set <code className="mono">TRADING_MODE=live</code> until all paper trading gates pass (4+ weeks).
          </span>
        </div>
      </div>

      {/* Dashboard password */}
      <div className="panel p-5 space-y-4">
        <div className="flex items-center gap-2">
          <Key size={14} style={{ color: "var(--purple)" }} />
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Dashboard Password</span>
        </div>
        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
          The dashboard password is set via the <code className="mono text-[10px] px-1 rounded" style={{ background: "var(--bg-surface-3)" }}>DASHBOARD_PASSWORD</code> environment variable.
          Restart the dashboard container after changing it.
        </p>
        <div className="relative">
          <input
            type={showPass ? "text" : "password"}
            placeholder="New password"
            value={newPass}
            onChange={e => setNewPass(e.target.value)}
            className="w-full px-3 py-2 pr-10 text-sm rounded outline-none"
            style={{ background: "var(--bg-surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          />
          <button
            type="button"
            onClick={() => setShowPass(!showPass)}
            className="absolute right-3 top-1/2 -translate-y-1/2"
            style={{ color: "var(--text-tertiary)" }}
          >
            {showPass ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
        <button
          onClick={savePassword}
          className="px-4 py-2 rounded text-xs font-semibold"
          style={{ background: "var(--bg-surface-3)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
        >
          {saved ? "✓ Set DASHBOARD_PASSWORD in .env to change" : "How to change password"}
        </button>
      </div>

      {/* Service URLs */}
      <div className="panel p-5 space-y-3">
        <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Service Endpoints</span>
        <div className="space-y-2 text-xs">
          {[
            { label: "Gateway", url: "http://localhost:8080" },
            { label: "Grafana", url: "http://localhost:3000" },
            { label: "Prometheus", url: "http://localhost:9090" },
            { label: "Qdrant", url: "http://localhost:6333/dashboard" },
            { label: "RAG API Docs", url: "http://localhost:8009/docs" },
          ].map(s => (
            <div key={s.label} className="flex justify-between items-center py-1.5 border-b" style={{ borderColor: "var(--border-subtle)" }}>
              <span style={{ color: "var(--text-secondary)" }}>{s.label}</span>
              <a href={s.url} target="_blank" rel="noopener noreferrer" className="mono hover:underline" style={{ color: "var(--blue)" }}>
                {s.url}
              </a>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
