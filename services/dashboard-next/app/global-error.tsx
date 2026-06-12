"use client";

// Last-resort boundary: catches errors in the root layout itself. Must render its
// own <html>/<body>. Only active in production builds.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body style={{ background: "#0a0e17", color: "#e6e9ef", fontFamily: "ui-sans-serif, system-ui, sans-serif" }}>
        <div style={{ display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center", padding: 24 }}>
          <div style={{ maxWidth: 480, textAlign: "center" }}>
            <h2 style={{ fontSize: 16, fontWeight: 600 }}>MeznaQuantFX — fatal error</h2>
            <p style={{ fontSize: 12, color: "#8b93a7", margin: "12px 0" }}>{error.message}</p>
            <button
              onClick={reset}
              style={{ background: "#0ea5e9", color: "#fff", border: 0, borderRadius: 6, padding: "8px 16px", fontSize: 13, cursor: "pointer" }}
            >
              Reload
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
