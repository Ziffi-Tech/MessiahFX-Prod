"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Lock, Eye, EyeOff, User } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");

    const res = await fetch("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });

    if (res.ok) {
      router.push("/");
      router.refresh();
    } else {
      setError("Invalid credentials");
    }
    setLoading(false);
  }

  return (
    <main
      className="min-h-dvh flex items-center justify-center p-4"
      style={{ background: "var(--bg-base)" }}
    >
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-10">
          <div
            className="inline-flex items-center justify-center w-14 h-14 rounded-2xl mb-4 text-white font-black text-xl"
            style={{ background: "var(--blue)" }}
          >
            MX
          </div>
          <h1 className="text-xl font-bold" style={{ color: "var(--text-primary)" }}>
            MeznaQuantFX
          </h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
            Algorithmic trading platform
          </p>
        </div>

        {/* Card */}
        <div className="panel p-6 space-y-5">
          <div>
            <h2 className="text-sm font-semibold mb-1" style={{ color: "var(--text-primary)" }}>
              Dashboard access
            </h2>
            <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
              Sign in with your operator credentials
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                Username
              </label>
              <div className="relative">
                <User
                  size={14}
                  className="absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--text-tertiary)" }}
                />
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Enter username"
                  autoComplete="username"
                  required
                  className="w-full pl-9 pr-3 py-2.5 text-sm rounded outline-none focus:ring-1"
                  style={{
                    background: "var(--bg-surface-2)",
                    border: `1px solid ${error ? "var(--red)" : "var(--border)"}`,
                    color: "var(--text-primary)",
                  }}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                Password
              </label>
              <div className="relative">
                <Lock
                  size={14}
                  className="absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--text-tertiary)" }}
                />
                <input
                  type={show ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter password"
                  required
                  className="w-full pl-9 pr-10 py-2.5 text-sm rounded outline-none focus:ring-1"
                  style={{
                    background: "var(--bg-surface-2)",
                    border: `1px solid ${error ? "var(--red)" : "var(--border)"}`,
                    color: "var(--text-primary)",
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShow(!show)}
                  className="absolute right-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  {show ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
              {error && (
                <p className="text-xs" style={{ color: "var(--red)" }}>
                  {error}
                </p>
              )}
            </div>

            <button
              type="submit"
              disabled={loading || !password || !username}
              className="w-full py-2.5 rounded text-sm font-semibold transition-opacity disabled:opacity-50"
              style={{ background: "var(--blue)", color: "#fff" }}
            >
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>

        {/* Status */}
        <div className="flex items-center justify-center gap-2 mt-6">
          <span className="live-dot" />
          <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            Paper trading mode active
          </span>
        </div>
      </div>
    </main>
  );
}
