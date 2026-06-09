# MeznaQuantFX — Trading Terminal (dashboard-next)

The production operator UI: a real-time, keyboard-driven trading cockpit built on
Next.js 16 / React 19. It replaces the legacy Streamlit panel (now behind the
`legacy-ui` profile).

## Real-time spine

The terminal is driven by a single Server-Sent Events stream, not HTTP polling.

```
exchanges ──CCXT Pro──► market-data ──► Redis (tick:latest:*, signals:opportunities, risk:state)
                                           │
                              gateway  GET /stream  (SSE aggregator)
                                           │  event: ticks | risk | signals
                                           ▼
                       dashboard-next  EventSource (one app-wide connection)
                                           ▼
                           zustand live store → PriceGrid, PriceChart, BotControls
```

### Endpoints added

| Service | Endpoint | Purpose |
|---|---|---|
| market-data | `GET /ticks/latest[?venues=]` | Snapshot of `tick:latest:*` (first paint + polling fallback) |
| market-data | `GET /orderbook/latest?venue=&symbol=` | L2 depth snapshot for the DOM panel (feed gated by `ORDERBOOK_SYMBOLS`) |
| gateway | `GET /stream` | SSE: `ticks` / `risk` / `signals` frames, ~1s cadence, auto-reconnect |
| journal | `GET /pnl/summary` | Now returns `win_rate`, `winning/losing_trades`, `average_win/loss`, `profit_factor`, `max_drawdown_pct`, `sharpe_ratio` |
| backtest | `GET /ohlcv` | Persisted candles (`ohlcv_bars`) — feeds the candlestick chart |

The SSE stream goes through the existing Next proxy unchanged
(`/api/gateway/stream` → gateway `/stream`). SSE was chosen over WebSocket so the
proxy, auth cookie, and auto-reconnect all work with no new dependency. A WS
upgrade for sub-second order flow can come later.

## Terminal features

- **Live market monitor** (PriceGrid): real bid/ask/mid + spread + tick direction.
- **Candlestick chart** (PriceChart): lightweight-charts v5 over persisted OHLCV,
  symbol selector, 1m/5m/15m/1h, live last-bar overlay from the SSE mid.
- **L2 depth ladder** (DepthLadder): real order book (bids/asks + cumulative depth
  bars + spread) for symbols in `ORDERBOOK_SYMBOLS`, polled 1s.
- **Global controls** (BotControls, topbar): START (`bot/start`, paper) / STOP
  (`bot/stop`) / KILL (`control/kill`) + PAPER/LIVE/HALTED badge + stream health.
- **⌘K command palette**: keyboard-first navigation + bot actions.

## Run it

Local (host-side dev, gateway running on :8080):

```bash
cd services/dashboard-next
pnpm install
pnpm dev          # http://localhost:3000  (proxies to GATEWAY_URL or :8080)
```

Containerised (default stack — terminal included):

```bash
podman-compose up -d            # builds + starts dashboard-next on :3001
# legacy Streamlit, if ever needed:
podman-compose --profile legacy-ui up -d dashboard
```

### Build notes

- Image build is `output: standalone` + pnpm@9 (matches lockfile 9.0).
- The repo-root **`.containerignore`** excludes `**/node_modules` / `**/.next` so
  the broad `COPY services/dashboard-next/ .` cannot clobber the Alpine deps with a
  host (glibc) `node_modules`. Do not remove it.
- `lib/` is committed (the root `.gitignore` Python `lib/` rule is anchored to
  `/lib/` so it no longer swallows the Next.js app core).

## Authentication & roles

Multi-user, signed-session auth. Login issues an HMAC-SHA256 signed token (a
minimal HS256 JWS, no dependency — runs in Edge middleware and Node alike) stored
in the `mxauth` httpOnly cookie. The middleware ([proxy.ts](../services/dashboard-next/proxy.ts))
verifies the signature + expiry on every request.

**Users** come from `DASHBOARD_USERS` (JSON array). **Roles**:

| Role | Can do |
|---|---|
| `admin` | Everything |
| `operator` | Everything (read + write/control actions) |
| `viewer` | Read-only — every mutating request is rejected 403; write UI is hidden |

RBAC is enforced server-side in the gateway proxy route (viewer → 403 on any
non-GET), and mirrored in the UI (BotControls + command palette hide write
actions for viewers). After auth, the proxy forwards `X-Mezna-User` /
`X-Mezna-Role`; the gateway control plane attributes kill-switch / start / stop /
toggle audit records to the **real user** instead of the literal `"dashboard"`.

### Setup

```bash
# 1. signing secret
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"   # → SESSION_SECRET

# 2. hash each operator's password
node services/dashboard-next/scripts/hash-password.mjs 'alice secret'        # → scrypt$...

# 3. roster (env)
DASHBOARD_USERS='[{"username":"alice","password":"scrypt$..","role":"admin"},
                  {"username":"bob","password":"scrypt$..","role":"viewer"}]'
```

If `DASHBOARD_USERS` is empty, the terminal falls back to legacy single-password
mode (`DASHBOARD_PASSWORD`, any username, admin role) so existing deployments keep
working — set the roster to disable it.

## Environment

| Var | Where | Notes |
|---|---|---|
| `GATEWAY_URL` | dashboard-next | In-container: `http://gateway:8000`. All `/api/gateway/*` routes here by default. |
| `SESSION_SECRET` | dashboard-next | HMAC key for session tokens. **Required in prod.** |
| `DASHBOARD_USERS` | dashboard-next | JSON roster (see above). Empty → legacy single-password mode. |
| `DASHBOARD_PASSWORD` | dashboard-next | Legacy fallback password (admin) when no roster. |
| `NEXT_PUBLIC_USE_SERVICE_ROUTING` | dashboard-next (dev only) | `true` fans out to individual service ports for isolated debugging; leave unset in prod. |
| `CORS_ORIGINS` | gateway | Includes `http://dashboard-next:3000`; tighten to the real origin in prod. |

## Known gaps / next

- Go-live gate unchanged: 4+ weeks clean paper + kill switch tested in prod.
