"use client";

import { useEffect, useRef, useState } from "react";
import type { StrategyProfile, RagResponse } from "@/types/api";
import { BookOpen, Upload, MessageSquare, Send, Trash2, ChevronRight, X } from "lucide-react";

export default function RagStudioPage() {
  const [profiles, setProfiles] = useState<StrategyProfile[]>([]);
  const [selected, setSelected] = useState<StrategyProfile | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<RagResponse | null>(null);
  const [querying, setQuerying] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadName, setUploadName] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadProfiles = async () => {
    const res = await fetch("/api/gateway/rag/strategies", { cache: "no-store" }).catch(() => null);
    if (res?.ok) setProfiles(await res.json());
  };

  useEffect(() => {
    let active = true;
    (async () => {
      const res = await fetch("/api/gateway/rag/strategies", { cache: "no-store" }).catch(() => null);
      if (active && res?.ok) setProfiles(await res.json());
    })();
    return () => { active = false; };
  }, []);

  const query = async () => {
    if (!question.trim()) return;
    setQuerying(true);
    setAnswer(null);
    try {
      const res = await fetch("/api/gateway/rag/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (res.ok) setAnswer(await res.json());
    } catch { /* */ } finally { setQuerying(false); }
  };

  const upload = async () => {
    if (!uploadFile || !uploadName) return;
    setUploading(true);
    const form = new FormData();
    form.append("file", uploadFile);
    form.append("title", uploadName);
    form.append("category", "strategy_note");
    await fetch("/api/gateway/rag/ingest/pdf", { method: "POST", body: form }).catch(() => { });
    await loadProfiles();
    setUploadFile(null);
    setUploadName("");
    setUploading(false);
  };

  const deleteProfile = async (id: string) => {
    await fetch(`/api/gateway/rag/strategies/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => { });
    await loadProfiles();
    if (selected?.source_id === id) setSelected(null);
  };

  const confidenceColor = (c: string) =>
    c === "high" ? "badge-green" : c === "medium" ? "badge-orange" : "badge-red";

  return (
    <div className="space-y-5">
      <h1 className="text-base font-bold" style={{ color: "var(--text-primary)" }}>
        RAG Studio
      </h1>
      <p className="text-xs -mt-3" style={{ color: "var(--text-secondary)" }}>
        Upload trading books and research. Claude extracts structured strategies from each document.
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
        {/* Left: library + upload */}
        <div className="lg:col-span-2 space-y-4">
          {/* Upload card */}
          <div className="panel p-4 space-y-3">
            <div className="flex items-center gap-2">
              <Upload size={13} style={{ color: "var(--blue)" }} />
              <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Upload PDF</span>
            </div>

            <input
              type="text"
              placeholder="Book title"
              value={uploadName}
              onChange={e => setUploadName(e.target.value)}
              className="w-full px-3 py-2 text-xs rounded outline-none"
              style={{
                background: "var(--bg-surface-2)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
              }}
            />

            <div
              className="border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors"
              style={{
                borderColor: uploadFile ? "var(--blue)" : "var(--border)",
                background: uploadFile ? "var(--blue-dim)" : "transparent",
              }}
              onClick={() => fileRef.current?.click()}
            >
              <input
                ref={fileRef}
                type="file"
                accept=".pdf"
                className="hidden"
                onChange={e => setUploadFile(e.target.files?.[0] ?? null)}
              />
              {uploadFile ? (
                <p className="text-xs" style={{ color: "var(--blue)" }}>
                  {uploadFile.name}
                </p>
              ) : (
                <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
                  Click to select PDF
                </p>
              )}
            </div>

            <button
              onClick={upload}
              disabled={uploading || !uploadFile || !uploadName}
              className="w-full py-2 text-xs font-semibold rounded disabled:opacity-40 transition-opacity"
              style={{ background: "var(--blue)", color: "#fff" }}
            >
              {uploading ? "Analysing with Claude…" : "Upload & Analyse"}
            </button>
          </div>

          {/* Library */}
          <div className="panel">
            <div className="px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
              <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
                Knowledge Base ({profiles.length})
              </span>
            </div>
            {profiles.length === 0 ? (
              <div className="p-6 text-center">
                <BookOpen size={24} className="mx-auto mb-2" style={{ color: "var(--text-tertiary)" }} />
                <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>No books uploaded yet</p>
              </div>
            ) : (
              <div className="divide-y" style={{ borderColor: "var(--border-subtle)" }}>
                {profiles.map(p => (
                  <div
                    key={p.source_id}
                    className="flex items-center gap-3 px-4 py-3 cursor-pointer transition-colors hover:bg-[var(--bg-hover)]"
                    style={selected?.source_id === p.source_id ? { background: "var(--blue-dim)" } : {}}
                    onClick={() => setSelected(p)}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium truncate" style={{ color: "var(--text-primary)" }}>
                        {p.title}
                      </p>
                      <p className="text-[10px] mt-0.5" style={{ color: "var(--text-secondary)" }}>
                        {p.strategy_name}
                      </p>
                    </div>
                    <span className={`badge ${confidenceColor(p.confidence)} shrink-0`}>{p.confidence}</span>
                    <button
                      onClick={e => { e.stopPropagation(); deleteProfile(p.source_id); }}
                      className="shrink-0 p-1 rounded hover:bg-[var(--red-dim)]"
                      style={{ color: "var(--text-tertiary)" }}
                    >
                      <Trash2 size={11} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right: Q&A + profile detail */}
        <div className="lg:col-span-3 space-y-4">
          {/* Q&A */}
          <div className="panel p-4 space-y-3">
            <div className="flex items-center gap-2">
              <MessageSquare size={13} style={{ color: "var(--purple)" }} />
              <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
                Ask the Knowledge Base
              </span>
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="e.g. What is the maximum risk per trade?"
                value={question}
                onChange={e => setQuestion(e.target.value)}
                onKeyDown={e => e.key === "Enter" && query()}
                className="flex-1 px-3 py-2 text-xs rounded outline-none"
                style={{
                  background: "var(--bg-surface-2)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                }}
              />
              <button
                onClick={query}
                disabled={querying || !question.trim()}
                className="px-3 py-2 rounded disabled:opacity-40 transition-opacity"
                style={{ background: "var(--purple)", color: "#fff" }}
              >
                {querying ? "…" : <Send size={13} />}
              </button>
            </div>

            {answer && (
              <div className="space-y-2">
                <div
                  className="p-3 rounded text-xs leading-relaxed"
                  style={{ background: "var(--bg-surface-2)", color: "var(--text-primary)", border: "1px solid var(--border-subtle)" }}
                >
                  {answer.answer}
                </div>
                {answer.sources.length > 0 && (
                  <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                    Sources: {answer.sources.map(s => s.title).join(" · ")}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Strategy profile */}
          {selected && (
            <div className="panel p-4 space-y-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-sm font-bold" style={{ color: "var(--text-primary)" }}>
                    {selected.strategy_name}
                  </h3>
                  <p className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
                    {selected.title}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className={`badge ${confidenceColor(selected.confidence)}`}>{selected.confidence}</span>
                  <button onClick={() => setSelected(null)} style={{ color: "var(--text-tertiary)" }}>
                    <X size={14} />
                  </button>
                </div>
              </div>

              <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                {selected.core_thesis}
              </p>

              <div className="grid grid-cols-2 gap-4 text-xs">
                <div>
                  <p className="font-semibold mb-1" style={{ color: "var(--text-secondary)" }}>ENTRY</p>
                  <ul className="space-y-0.5">
                    {selected.entry_criteria.map((c, i) => (
                      <li key={i} className="flex items-start gap-1" style={{ color: "var(--text-primary)" }}>
                        <ChevronRight size={10} className="mt-0.5 shrink-0" style={{ color: "var(--green)" }} />
                        {c}
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="font-semibold mb-1" style={{ color: "var(--text-secondary)" }}>EXIT</p>
                  <ul className="space-y-0.5">
                    {selected.exit_criteria.map((c, i) => (
                      <li key={i} className="flex items-start gap-1" style={{ color: "var(--text-primary)" }}>
                        <ChevronRight size={10} className="mt-0.5 shrink-0" style={{ color: "var(--red)" }} />
                        {c}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>

              {selected.risk_rules.max_risk_per_trade_pct && (
                <div
                  className="px-3 py-2 rounded text-xs"
                  style={{ background: "var(--green-dim)", border: "1px solid rgba(0,229,160,0.2)" }}
                >
                  <span style={{ color: "var(--green)" }}>Risk: </span>
                  <span style={{ color: "var(--text-primary)" }}>
                    Max {selected.risk_rules.max_risk_per_trade_pct}% per trade
                    {selected.risk_rules.max_drawdown_pct ? ` · ${selected.risk_rules.max_drawdown_pct}% max drawdown` : ""}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
