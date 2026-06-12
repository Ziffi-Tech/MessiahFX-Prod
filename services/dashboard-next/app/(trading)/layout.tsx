import { Sidebar } from "@/components/layout/sidebar";
import { Topbar }  from "@/components/layout/topbar";
import { StreamConnector } from "@/components/trading/stream-connector";
import { CommandPalette } from "@/components/layout/command-palette";
import { WorkspaceHydration } from "@/components/trading/workspace-switcher";

export default function TradingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-dvh overflow-hidden">
      {/* Opens the single app-wide SSE connection (ticks / risk / signals). */}
      <StreamConnector />
      {/* ⌘K / Ctrl-K command palette — keyboard-first navigation + actions. */}
      <CommandPalette />
      {/* Rehydrate the persisted workspace after mount (no SSR mismatch). */}
      <WorkspaceHydration />
      {/* Keyboard users can jump straight past the nav. */}
      <a href="#main" className="skip-link">Skip to content</a>
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Topbar />
        <main
          id="main"
          className="flex-1 overflow-y-auto p-6"
          style={{ background: "var(--bg-base)" }}
        >
          {children}
        </main>
      </div>
    </div>
  );
}
